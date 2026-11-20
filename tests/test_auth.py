"""Auth: password hashing, session tokens, the default account, the HTTP gates."""

import base64
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from steward.config import Config
from steward.session.store import SessionStore
from steward.web.auth import (
    MIN_PASSWORD_LENGTH,
    ensure_default_user,
    hash_password,
    make_token,
    verify_password,
    verify_token,
)


class TestPasswordHashing(unittest.TestCase):
    def test_round_trip(self) -> None:
        stored = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", stored))
        self.assertFalse(verify_password("correct horse battery stapler", stored))

    def test_same_password_hashes_differ_by_salt(self) -> None:
        self.assertNotEqual(hash_password("same"), hash_password("same"))

    def test_malformed_hash_fails_closed(self) -> None:
        self.assertFalse(verify_password("x", ""))
        self.assertFalse(verify_password("x", "md5$deadbeef"))
        self.assertFalse(verify_password("x", "pbkdf2_sha256$abc$zz$zz"))


class TestTokens(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = bytes(range(32))

    def test_round_trip(self) -> None:
        token = make_token("admin", self.secret)
        self.assertEqual(verify_token(token, self.secret), "admin")

    def test_wrong_secret_is_rejected(self) -> None:
        token = make_token("admin", self.secret)
        self.assertIsNone(verify_token(token, bytes(range(31, -1, -1))))

    def test_tampered_payload_is_rejected(self) -> None:
        token = make_token("admin", self.secret)
        header, payload, signature = token.split(".")
        forged = json.loads(base64.urlsafe_b64decode(payload + "=="))
        forged["sub"] = "attacker"
        encoded = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=")
        self.assertIsNone(verify_token(f"{header}.{encoded.decode()}.{signature}", self.secret))

    def test_expired_token_is_rejected(self) -> None:
        token = make_token("admin", self.secret, ttl=-1)
        self.assertIsNone(verify_token(token, self.secret))

    def test_garbage_is_rejected(self) -> None:
        self.assertIsNone(verify_token("not-a-jwt", self.secret))
        self.assertIsNone(verify_token("a.b", self.secret))


def make_config(root: Path) -> Config:
    config = Config(
        workspace=root / "workspace",
        data_dir=root / "data",
        model="fake",
        permission="workspace-write",
    )
    return config.prepare()


class HttpTestCase(unittest.TestCase):
    """An app whose users table holds exactly the default account."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = make_config(Path(self._tmp.name))
        store = SessionStore(self.config.db_path)
        created = ensure_default_user(store)
        assert created is not None
        self.default_password = created[1]
        store.close()

        from steward.web.api import create_app

        self.app = create_app(self.config)
        self.client = cast(Any, TestClient(self.app))
        _ = self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self._tmp.cleanup()

    def login(self, username: str = "admin", password: str | None = None) -> Any:
        return self.client.post(
            "/api/login",
            json={"username": username, "password": password or self.default_password},
        )


class TestDefaultAccount(unittest.TestCase):
    def test_created_once_with_a_random_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "test.db")
            first = ensure_default_user(store)
            self.assertIsNotNone(first)
            self.assertEqual(ensure_default_user(store), None)
            self.assertEqual(store.user_count(), 1)
            store.close()

    def test_secret_persists_across_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from steward.web.auth import load_or_create_secret

            first = load_or_create_secret(Path(tmp))
            self.assertEqual(load_or_create_secret(Path(tmp)), first)


class TestHttpAuth(HttpTestCase):
    def test_protected_endpoints_need_login(self) -> None:
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.json())

    def test_login_sets_an_httponly_cookie(self) -> None:
        response = self.login()
        self.assertEqual(response.status_code, 200)
        header = response.headers["set-cookie"]
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=strict", header)
        self.assertIn("steward_session=", header)

    def test_login_then_me(self) -> None:
        _ = self.login()
        data = self.client.get("/api/me").json()
        self.assertEqual(data["username"], "admin")

    def test_wrong_password_is_a_generic_401(self) -> None:
        response = self.login(password="not-the-password")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("password", response.json()["error"].lower())

    def test_unknown_user_is_the_same_401(self) -> None:
        response = self.login(username="nobody")
        self.assertEqual(response.status_code, 401)

    def test_repeated_failures_lock_the_username(self) -> None:
        for _ in range(5):
            _ = self.login(password="wrong")
        locked = self.login(password=self.default_password)
        self.assertEqual(locked.status_code, 401)
        self.assertIn("稍后再试", locked.json()["error"])

    def test_change_password_flow(self) -> None:
        _ = self.login()
        new_password = "brand-new-password"
        response = self.client.post(
            "/api/password",
            json={"current_password": self.default_password, "new_password": new_password},
        )
        self.assertEqual(response.status_code, 200)

        # The old password no longer logs in; the new one does.
        self.assertEqual(self.login(password=self.default_password).status_code, 401)
        self.assertEqual(self.login(password=new_password).status_code, 200)

    def test_change_password_requires_the_current_one(self) -> None:
        _ = self.login()
        response = self.client.post(
            "/api/password",
            json={"current_password": "wrong", "new_password": "brand-new-password"},
        )
        self.assertEqual(response.status_code, 400)

    def test_new_password_has_a_minimum_length(self) -> None:
        _ = self.login()
        response = self.client.post(
            "/api/password",
            json={"current_password": self.default_password, "new_password": "short" * 1},
        )
        self.assertEqual(response.status_code, 400)
        self.assertLess(len("short"), MIN_PASSWORD_LENGTH)

    def test_static_page_stays_public(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_logout_clears_the_session(self) -> None:
        _ = self.login()
        self.assertEqual(self.client.get("/api/me").status_code, 200)

        response = self.client.post("/api/logout")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_jwt_secret_file_is_written_with_restricted_permissions(self) -> None:
        secret_path = self.config.data_dir / "jwt_secret"
        self.assertTrue(secret_path.is_file())
        self.assertLess(secret_path.stat().st_mode & 0o777, 0o644)


if __name__ == "__main__":
    unittest.main()
