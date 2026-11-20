"""Gateway-specific request headers.

OpenCode Go (opencode.ai) asks coding-agent clients to:

1. send a stable per-conversation session id in ``x-opencode-session`` so the
   gateway can optimize routing and prompt caching;
2. identify themselves with a real product User-Agent instead of a generic
   HTTP-library name.

The session token is a hash of the session id, so raw chat / session ids
never leave the process. Requests outside any conversation (provider tests,
model sync) use a process-level fallback so they still look stable.

Mirrors the mapping used by CowAgent's ``openai_http_client``: known gateway
hosts get the extra headers; every other host is untouched.
"""

from __future__ import annotations

import hashlib
import uuid
from urllib.parse import urlparse

from steward import __version__

_SESSION_HEADER_BY_HOST: dict[str, str] = {
    "opencode.ai": "x-opencode-session",
}

_PROCESS_SESSION_ID = uuid.uuid4().hex


def _session_token(session_id: str) -> str:
    scope = session_id or _PROCESS_SESSION_ID
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:32]


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def gateway_headers(url: str, session_id: str = "") -> dict[str, str]:
    """Headers a known gateway expects for ``url``; ``{}`` for any other host."""
    host = _host_of(url)
    if not host:
        return {}
    for suffix, header in _SESSION_HEADER_BY_HOST.items():
        if _host_matches(host, suffix):
            return {
                header: _session_token(session_id),
                "User-Agent": f"steward/{__version__}",
            }
    return {}
