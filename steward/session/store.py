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
"""


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
