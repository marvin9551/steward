"""Login: password hashing, session tokens, the default account.

Everything here is stdlib — no new dependency for the web extra:

- passwords: PBKDF2-HMAC-SHA256 with a per-user random salt, stored as
  ``pbkdf2_sha256$<iterations>$<salt>$<hash>`` and compared in constant time;
- sessions: an HS256 JWT built from ``hmac`` + base64url. The signing secret
  is 32 random bytes persisted as hex in ``<data_dir>/jwt_secret`` with 0600
  permissions, so sessions survive restarts but never leave the machine;
- transport: the token lives in an HttpOnly, SameSite=Strict cookie, so
  JavaScript never touches it and cross-site POSTs don't carry it.

The default account is created on first start with a random password the CLI
prints once; users change it from the settings dialog. Only ``cli.py`` prints,
per decision 8 — this module returns the credentials instead.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any, cast

from steward.session.store import SessionStore

SESSION_COOKIE = "steward_session"
TOKEN_TTL_SECONDS = 7 * 24 * 3600
PBKDF2_ITERATIONS = 210_000
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD_BYTES = 8
MIN_PASSWORD_LENGTH = 8


# -- passwords --------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time comparison; malformed hashes simply fail."""
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    try:
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected = bytes.fromhex(parts[3])
    except ValueError:
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(derived, expected)


# -- tokens -----------------------------------------------------------

def load_or_create_secret(data_dir: Path) -> bytes:
    """The JWT signing secret: 32 random bytes as hex, mode 0600 on disk."""
    path = data_dir / "jwt_secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            return bytes.fromhex(path.read_text(encoding="ascii").strip())
        except ValueError:
            pass  # unreadable/corrupt: rotate to a fresh secret below
    secret = secrets.token_bytes(32)
    path.write_text(secret.hex(), encoding="ascii")
    path.chmod(0o600)
    return secret


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(signing_input: str, secret: bytes) -> str:
    digest = hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest()
    return _b64url(digest)


def make_token(username: str, secret: bytes, ttl: int = TOKEN_TTL_SECONDS) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": username, "iat": now, "exp": now + ttl}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    return f"{signing_input}.{_sign(signing_input, secret)}"


def verify_token(token: str, secret: bytes) -> str | None:
    """Return the token's subject, or None when anything fails to check out."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, signature = parts
    if not hmac.compare_digest(signature, _sign(f"{header_b64}.{payload_b64}", secret)):
        return None
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(header, dict) or not isinstance(payload, dict):
        return None
    claims = cast(dict[str, Any], payload)
    if cast(dict[str, Any], header).get("alg") != "HS256":
        return None
    expires = claims.get("exp")
    if not isinstance(expires, int) or expires < int(time.time()):
        return None
    subject = claims.get("sub")
    return subject if isinstance(subject, str) else None


# -- the default account ----------------------------------------------

def ensure_default_user(store: SessionStore) -> tuple[str, str] | None:
    """Create the first account when the users table is empty.

    Returns ``(username, password)`` for the CLI banner, or ``None`` when an
    account already exists — the generated password is never stored in plain
    text and only ever surfaced through this return value.
    """
    if store.user_count() > 0:
        return None
    password = secrets.token_urlsafe(DEFAULT_PASSWORD_BYTES)
    store.create_user(DEFAULT_USERNAME, hash_password(password))
    return (DEFAULT_USERNAME, password)
