"""Append-only conversation storage in SQLite.

Two design choices worth noting:

1. We store the message payload the model saw, not the rendered UI text. A
   transcript you can replay is the difference between debugging and guessing.
2. Writes are append-only and ordered by `seq`; there is no update path. That
   keeps replay deterministic and makes a future branch/fork feature a matter
   of copying a prefix.

One connection with an internal lock, rather than a connection per thread: the
web front end reads (sidebar, transcript) while a turn writes, and SQLite locks
the file, not the Python object. Serializing here keeps that safe without
making callers care.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, cast

_DDL = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    seq         INTEGER NOT NULL,
    role        TEXT    NOT NULL,
    payload     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_messages_session_seq
    ON messages(session_id, seq);
CREATE INDEX IF NOT EXISTS ix_messages_session
    ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_profiles (
    name         TEXT PRIMARY KEY,
    base_url     TEXT    NOT NULL DEFAULT '',
    model        TEXT    NOT NULL DEFAULT '',
    api_key      TEXT    NOT NULL DEFAULT '',
    temperature  REAL,
    stream_usage INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL,
    level       TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    event       TEXT    NOT NULL,
    session_id  TEXT    NOT NULL DEFAULT '',
    message     TEXT    NOT NULL,
    detail      TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_logs_id_desc
    ON logs(id DESC);
CREATE INDEX IF NOT EXISTS ix_logs_filter
    ON logs(category, level, session_id, id DESC);
"""

MAX_LOG_ROWS = 10_000
_SENSITIVE_MARKERS = ("api_key", "secret", "password", "token", "authorization")


def _redact_detail(value: Any) -> Any:
    """Recursively mask sensitive keys before persisting log details."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for k, v in cast(dict[str, Any], value).items():
            key_str = str(k)
            lowered = key_str.lower()
            if any(marker in lowered for marker in _SENSITIVE_MARKERS):
                result[key_str] = "***"
            else:
                result[key_str] = _redact_detail(v)
        return result
    if isinstance(value, list):
        return [_redact_detail(item) for item in cast(list[Any], value)]
    return value


class SessionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.commit()

    # -- writes ---------------------------------------------------------

    def append(self, session_id: str, role: str, payload: dict[str, Any]) -> int:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS seq FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            seq = int(row["seq"]) + 1
            self._conn.execute(
                "INSERT INTO messages(session_id, seq, role, payload, created_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (session_id, seq, role, json.dumps(payload, ensure_ascii=False)),
            )
        return seq

    # -- reads ----------------------------------------------------------

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        """Return the session as OpenAI-style chat messages."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, payload FROM messages WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
        return [_to_message(row["role"], json.loads(row["payload"])) for row in rows]

    def latest_session(self) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id FROM messages ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row["session_id"] if row else None

    def exists(self, session_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM messages WHERE session_id = ? LIMIT 1", (session_id,)
            ).fetchone()
        return row is not None

    def sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT session_id,
                       COUNT(*)              AS messages,
                       MAX(created_at)       AS updated_at,
                       (SELECT role FROM messages m2
                         WHERE m2.session_id = m.session_id
                         ORDER BY seq DESC LIMIT 1) AS last_role,
                       (SELECT substr(payload, 1, 120) FROM messages m3
                         WHERE m3.session_id = m.session_id
                         ORDER BY seq DESC LIMIT 1) AS last_payload
                FROM messages m
                GROUP BY session_id
                ORDER BY MAX(id) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        # sqlite3.Row is not typed as a mapping, but dict(row) yields the
        # column-name -> value mapping the DDL declares.
        return [cast(dict[str, Any], dict(row)) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- users ----------------------------------------------------------

    def user_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])

    def get_user(self, username: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, username, password_hash FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        return cast(dict[str, Any], dict(row)) if row else None

    def create_user(self, username: str, password_hash: str) -> dict[str, Any]:
        """Insert one user; the caller owns uniqueness handling."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO users(username, password_hash, created_at, updated_at) "
                "VALUES (?, ?, datetime('now'), datetime('now'))",
                (username, password_hash),
            )
        user = self.get_user(username)
        assert user is not None
        return user

    def update_password(self, username: str, password_hash: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = datetime('now') "
                "WHERE username = ?",
                (password_hash, username),
            )
        return cursor.rowcount > 0

    # -- settings (key/value) -------------------------------------------

    def settings(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def set_settings(self, values: dict[str, str]) -> None:
        if not values:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = datetime('now')",
                [(key, value) for key, value in values.items()],
            )

    # -- provider profiles ----------------------------------------------

    def profiles(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, base_url, model, api_key, temperature, stream_usage "
                "FROM provider_profiles ORDER BY name"
            ).fetchall()
        return [cast(dict[str, Any], dict(row)) for row in rows]

    def get_profile(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT name, base_url, model, api_key, temperature, stream_usage "
                "FROM provider_profiles WHERE name = ?",
                (name,),
            ).fetchone()
        return cast(dict[str, Any], dict(row)) if row else None

    def upsert_profile(self, profile: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO provider_profiles(name, base_url, model, api_key, "
                "temperature, stream_usage, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now')) "
                "ON CONFLICT(name) DO UPDATE SET base_url = excluded.base_url, "
                "model = excluded.model, api_key = excluded.api_key, "
                "temperature = excluded.temperature, stream_usage = excluded.stream_usage, "
                "updated_at = datetime('now')",
                (
                    str(profile["name"]),
                    str(profile.get("base_url") or ""),
                    str(profile.get("model") or ""),
                    str(profile.get("api_key") or ""),
                    profile.get("temperature"),
                    1 if profile.get("stream_usage") else 0,
                ),
            )

    def delete_profile(self, name: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM provider_profiles WHERE name = ?", (name,)
            )
        return cursor.rowcount > 0

    # -- logs -----------------------------------------------------------

    def append_log(
        self,
        level: str,
        category: str,
        event: str,
        message: str,
        *,
        session_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> int:
        safe_detail = _redact_detail(detail or {})
        detail_json = json.dumps(safe_detail, ensure_ascii=False, default=str)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO logs(created_at, level, category, event, session_id, message, detail) "
                "VALUES (datetime('now'), ?, ?, ?, ?, ?, ?)",
                (
                    level.upper(),
                    category,
                    event,
                    session_id,
                    message,
                    detail_json,
                ),
            )
            log_id = int(cursor.lastrowid or 0)
            if log_id % 100 == 0:
                self._conn.execute(
                    "DELETE FROM logs WHERE id <= ("
                    "SELECT id FROM logs ORDER BY id DESC LIMIT 1 OFFSET ?"
                    ")",
                    (MAX_LOG_ROWS,),
                )
        return log_id

    def query_logs(
        self,
        *,
        level: str = "",
        category: str = "",
        session_id: str = "",
        query: str = "",
        before_id: int | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        capped = max(1, min(int(limit), 500))
        clauses: list[str] = []
        params: list[Any] = []

        if level.strip():
            clauses.append("level = ?")
            params.append(level.strip().upper())
        if category.strip():
            clauses.append("category = ?")
            params.append(category.strip())
        if session_id.strip():
            clauses.append("session_id LIKE ?")
            params.append(f"%{session_id.strip()}%")
        if query.strip():
            like = f"%{query.strip()}%"
            clauses.append(
                "(message LIKE ? OR event LIKE ? OR detail LIKE ? OR session_id LIKE ?)"
            )
            params.extend([like, like, like, like])
        if before_id is not None:
            clauses.append("id < ?")
            params.append(int(before_id))

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT id, created_at, level, category, event, session_id, message, detail "
            f"FROM logs {where_sql} ORDER BY id DESC LIMIT ?"
        )
        params.append(capped + 1)

        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()

        has_more = len(rows) > capped
        sliced = rows[:capped]
        items: list[dict[str, Any]] = []
        for row in sliced:
            raw_detail = str(row["detail"] or "{}")
            try:
                parsed_detail: Any = json.loads(raw_detail)
            except ValueError:
                parsed_detail = {"_raw": raw_detail}
            if not isinstance(parsed_detail, dict):
                parsed_detail = {"_raw": parsed_detail}
            items.append(
                {
                    "id": int(row["id"]),
                    "created_at": str(row["created_at"]),
                    "level": str(row["level"]),
                    "category": str(row["category"]),
                    "event": str(row["event"]),
                    "session_id": str(row["session_id"]),
                    "message": str(row["message"]),
                    "detail": cast(dict[str, Any], parsed_detail),
                }
            )
        return {"logs": items, "has_more": has_more}

    def clear_logs(self) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM logs")
        return max(0, int(cursor.rowcount))

    # -- helpers --------------------------------------------------------

    @staticmethod
    def new_session_id() -> str:
        stamp = time.strftime("%Y%m%d-%H%M")
        return f"{stamp}-{uuid.uuid4().hex[:4]}"


def _to_message(role: str, payload: dict[str, Any]) -> dict[str, Any]:
    if role == "assistant":
        message = {"role": "assistant", "content": payload.get("content") or ""}
        if payload.get("tool_calls"):
            message["tool_calls"] = payload["tool_calls"]
        return message
    if role == "tool":
        return {
            "role": "tool",
            "tool_call_id": payload.get("tool_call_id", ""),
            "content": payload.get("content", ""),
        }
    return {"role": "user", "content": payload.get("content", "")}
