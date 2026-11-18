"""The web back end, minus HTTP.

It owns three pieces of shared state:

- one `SessionStore` (thread-safe internally, so reads and the turn's writes can
  interleave safely),
- an `EventLog` the SSE endpoint tails,
- an `ApprovalRegistry` the runner awaits,
- a single turn slot, because one person asking one agent one question at a time
  is the honest model here; a second concurrent turn would interleave writes
  into the same session and produce a transcript nobody wants.

HTTP handlers call this; `tests/test_web.py` calls it directly, which is why the
interesting behavior is testable without opening a socket.
"""

from __future__ import annotations

import asyncio
import json
import threading

from steward import __version__
from steward.config import PERMISSION_MODES, Config
from steward.core.runner import Runner
from steward.providers.base import Provider, ProviderError
from steward.session.store import SessionStore
from steward.web.approvals import (
    DECISION_ALWAYS,
    DECISION_DENY,
    DECISION_ONCE,
    ApprovalRegistry,
)
from steward.web.stream import EventLog, encode

APPROVAL_TIMEOUT = 900.0


class Busy(RuntimeError):
    """A turn is already running."""


class UnknownSession(LookupError):
    """No stored messages for this session id."""


class WebService:
    def __init__(self, config: Config, provider: Provider | None = None) -> None:
        self.config = config
        self.events = EventLog()
        self.approvals = ApprovalRegistry()
        self.store = SessionStore(config.db_path)
        self._provider = provider
        self._turn = threading.Lock()
        self._busy_session: str | None = None
        self._always_allow: set[str] = set()

    # -- lifecycle ------------------------------------------------------

    def close(self) -> None:
        self.approvals.cancel_all()
        try:
            self.store.close()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            pass

    # -- reads ----------------------------------------------------------

    def status(self) -> dict:
        """Everything the page needs to describe itself. Never includes the key."""
        return {
            "version": __version__,
            "model": self.config.model,
            "base_url": self.config.base_url,
            "permission": self.config.permission,
            "permissions": list(PERMISSION_MODES),
            "workspace": str(self.config.workspace),
            "show_reasoning": self.config.show_reasoning,
            "has_api_key": bool(self.config.api_key),
            "busy": self._turn.locked(),
            "busy_session": self._busy_session,
            "latest_session": self.store.latest_session(),
        }

    def sessions(self, limit: int = 50) -> list[dict]:
        items = self.store.sessions(limit=limit)
        for item in items:
            item["preview"] = _preview(item.pop("last_payload", None))
        return items

    def transcript(self, session_id: str) -> dict:
        """Stored messages rendered as UI blocks.

        The page and the live stream must agree on shape, so a reload after a
        turn looks exactly like having watched it.
        """
        messages = self.store.messages(session_id)
        if not messages:
            raise UnknownSession(session_id)
        return {"session": session_id, "blocks": _blocks(messages)}

    # -- writes ---------------------------------------------------------

    def new_session(self) -> str:
        return SessionStore.new_session_id()

    def set_permission(self, mode: str) -> str:
        if mode not in PERMISSION_MODES:
            raise ValueError(f"permission must be one of {PERMISSION_MODES}, got {mode!r}")
        self.config.permission = mode
        self.events.publish({"type": "config", "permission": mode})
        return mode

    def start_turn(self, session_id: str, text: str) -> str:
        text = text.strip()
        if not text:
            raise ValueError("message is empty")
        if not self._turn.acquire(blocking=False):
            raise Busy("a turn is already running; wait for it to finish")
        session_id = session_id or self.new_session()
        self._busy_session = session_id
        self.events.publish({"type": "turn_start", "session": session_id, "text": text})
        try:
            worker = threading.Thread(
                target=self._worker,
                args=(session_id, text),
                name=f"steward-turn-{session_id}",
                daemon=True,
            )
            worker.start()
        except Exception:
            self._release()
            raise
        return session_id

    def resolve_approval(self, approval_id: str, decision: str) -> bool:
        return self.approvals.resolve(approval_id, decision)

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until no turn is running. Used by tests and shutdown."""
        if self._turn.acquire(timeout=-1 if timeout is None else timeout):
            self._turn.release()
            return True
        return False

    # -- turn execution -------------------------------------------------

    def _worker(self, session_id: str, text: str) -> None:
        try:
            asyncio.run(self._run_turn(session_id, text))
        except ProviderError as exc:
            self._publish({"type": "error", "text": f"provider error: {exc}"})
        except Exception as exc:  # noqa: BLE001 - the page shows what broke
            self._publish({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
        finally:
            self._release()

    async def _run_turn(self, session_id: str, text: str) -> None:
        runner = Runner(
            self.config,
            self.store,
            on_event=self._publish,
            approver=self._approve,
            provider=self._provider,
        )
        await runner.run_turn(session_id, text)

    def _release(self) -> None:
        self._busy_session = None
        self._publish({"type": "idle"})
        if self._turn.locked():
            self._turn.release()

    async def _approve(self, name: str, args: dict) -> bool:
        if name in self._always_allow:
            return True
        approval = self.approvals.create(name, args)
        self._publish({"type": "approval_request", **approval.to_dict()})
        decision = await asyncio.to_thread(
            self.approvals.wait, approval.id, APPROVAL_TIMEOUT
        )
        self._publish({"type": "approval_resolved", "id": approval.id, "decision": decision or DECISION_DENY})
        if decision == DECISION_ALWAYS:
            self._always_allow.add(name)
        return decision in (DECISION_ONCE, DECISION_ALWAYS)

    def _publish(self, payload) -> int:
        # The runner hands us typed events; the service itself publishes dicts.
        data = payload if isinstance(payload, dict) else encode(payload)
        return self.events.publish(data)


def _blocks(messages: list[dict]) -> list[dict]:
    """Flatten OpenAI-style messages into the page's block list."""
    blocks: list[dict] = []
    by_call: dict[str, dict] = {}

    for message in messages:
        role = message.get("role")
        content = str(message.get("content") or "")

        if role == "user":
            blocks.append({"kind": "user", "text": content})
        elif role == "assistant":
            if content.strip():
                blocks.append({"kind": "assistant", "text": content})
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                block = {
                    "kind": "tool",
                    "call_id": call.get("id") or "",
                    "name": function.get("name") or "",
                    "args": _parse_args(function.get("arguments")),
                    "output": "",
                    "ok": True,
                }
                blocks.append(block)
                if block["call_id"]:
                    by_call[block["call_id"]] = block
        elif role == "tool":
            block = by_call.get(message.get("tool_call_id") or "")
            if block is None:
                block = {
                    "kind": "tool",
                    "call_id": message.get("tool_call_id") or "",
                    "name": "",
                    "args": {},
                    "output": "",
                    "ok": True,
                }
                blocks.append(block)
            block["output"] = content
            block["ok"] = not content.startswith("ERROR:")

    return blocks


def _parse_args(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or "{}"))
    except ValueError:
        return {"_raw": str(raw)}
    return value if isinstance(value, dict) else {"_raw": value}


def _preview(raw: object, limit: int = 60) -> str:
    if not raw:
        return ""
    try:
        payload = json.loads(str(raw))
        text = str(payload.get("content") or "")
    except ValueError:
        text = str(raw)
    return " ".join(text.split())[:limit]
