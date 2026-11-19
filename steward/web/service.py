"""The web back end, minus HTTP.

Everything here runs on the API's event loop: a turn is an `asyncio.Task`, an
approval is a future, and the store is shared (it serializes internally). No
worker threads, no thread-safe queues — which is the main thing FastAPI buys
over the previous `http.server` version.

State owned here:

- one `SessionStore`, so the page's reads and the turn's writes agree,
- an `EventLog` the SSE endpoint tails,
- an `ApprovalRegistry` the runner awaits,
- a single turn slot: one person asking one agent one question at a time. A
  second concurrent turn would interleave writes into one session and produce a
  transcript nobody wants, so it gets 409 instead.

`tests/test_web.py` drives this directly, which is why the interesting behavior
is testable without opening a socket.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from steward import __version__
from steward.config import PERMISSION_MODES, Config, save_overlay
from steward.core.runner import Runner
from steward.providers.base import Provider, ProviderError
from steward.providers.openai_compat import stream_chat
from steward.session.store import SessionStore
from steward.web.approvals import (
    DECISION_ALWAYS,
    DECISION_DENY,
    DECISION_ONCE,
    ApprovalRegistry,
)
from steward.web.stream import EventLog, encode

APPROVAL_TIMEOUT = 900.0
PROVIDER_FIELDS = ("base_url", "model", "api_key", "temperature", "stream_usage")
MODELS_TIMEOUT = 15.0


class Busy(RuntimeError):
    """A turn is already running."""


class UnknownSession(LookupError):
    """No stored messages for this session id."""


class WebService:
    def __init__(self, config: Config, provider: Provider | None = None, probe=None) -> None:
        self.config = config
        self.events = EventLog()
        self.approvals = ApprovalRegistry()
        self.store = SessionStore(config.db_path)
        self._provider = provider
        self._probe = probe or stream_chat
        self._fetch_models = fetch_model_ids
        self._task: asyncio.Task | None = None
        self._busy_session: str | None = None
        self._always_allow: set[str] = set()

    # -- lifecycle ------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    async def close(self) -> None:
        self.approvals.cancel_all()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown
                pass
        self.store.close()

    async def wait_idle(self, timeout: float | None = None) -> bool:
        """Wait for the running turn. Used by tests and shutdown."""
        task = self._task
        if task is None or task.done():
            return True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except asyncio.TimeoutError:
            return False
        except Exception:  # noqa: BLE001 - the worker already reported it
            return True
        return True

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
            "busy": self.busy,
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

    # -- provider settings ----------------------------------------------

    def provider_settings(self) -> dict:
        """What the settings dialog shows. The key itself never leaves."""
        return {
            "base_url": self.config.base_url,
            "model": self.config.model,
            "temperature": self.config.temperature,
            "stream_usage": self.config.stream_usage,
            "api_key_set": bool(self.config.api_key),
            # Fields pinned by STEWARD_* env vars; the page cannot win those.
            "env_overrides": [name for name in self.config.env_overrides if name in PROVIDER_FIELDS],
        }

    def set_provider(self, updates: dict) -> dict:
        """Validate, apply, and persist page edits. Empty `api_key` means keep."""
        clean: dict = {}
        for name in PROVIDER_FIELDS:
            if name not in updates or updates[name] is None:
                continue
            if name in self.config.env_overrides:
                raise ValueError(f"{name} is set by STEWARD_* environment and cannot be changed here")
            value = updates[name]
            if name == "api_key":
                if value == "":
                    continue  # "keep the current key"
                clean[name] = str(value)
            elif name == "base_url":
                text = str(value).strip()
                if not text.startswith(("http://", "https://")):
                    raise ValueError("base_url must start with http:// or https://")
                clean[name] = text.rstrip("/")
            elif name == "model":
                text = str(value).strip()
                if not text:
                    raise ValueError("model must not be empty")
                clean[name] = text
            elif name == "temperature":
                number = float(value)
                if not 0.0 <= number <= 2.0:
                    raise ValueError("temperature must be between 0 and 2")
                clean[name] = number
            elif name == "stream_usage":
                clean[name] = bool(value)

        for name, value in clean.items():
            setattr(self.config, name, value)
        if clean:
            save_overlay(self.config, "provider", clean)

        payload = {"type": "provider", **self.provider_settings()}
        self.events.publish(payload)
        return self.provider_settings()

    async def probe_provider(self) -> dict:
        """One tiny real request, so a saved key is verified, not assumed."""
        try:
            result = await self._probe(
                self.config,
                [{"role": "user", "content": "Reply with exactly: pong"}],
                [],
            )
            reply = " ".join((result.text or "").split())
            return {"ok": True, "reply": reply[:80]}
        except ProviderError as exc:
            return {"ok": False, "error": str(exc)[:300]}

    async def list_models(self, base_url: str | None = None, api_key: str | None = None) -> dict:
        """Ask the gateway for its model list (`GET {base_url}/models`).

        Unsaved form values may be passed in, so the flow is
        "paste URL + key → sync → pick → save" in one visit. An empty key falls
        back to the saved one; env-locked fields ignore the override.
        """
        url = base_url.strip() if base_url else ""
        if not url or "base_url" in self.config.env_overrides:
            url = self.config.base_url
        if not url.startswith(("http://", "https://")):
            return {"models": [], "error": "base_url must start with http:// or https://"}

        key = api_key or ""
        if not key or "api_key" in self.config.env_overrides:
            key = self.config.api_key
        if not key:
            return {"models": [], "error": "api_key is empty: set it before syncing"}

        try:
            return {"models": await self._fetch_models(url.rstrip("/") + "/models", key)}
        except Exception as exc:  # noqa: BLE001 - surfaced to the dialog
            return {"models": [], "error": f"{type(exc).__name__}: {exc}"[:300]}

    # -- turn execution -------------------------------------------------

    async def start_turn(self, session_id: str, text: str) -> str:
        text = text.strip()
        if not text:
            raise ValueError("message is empty")
        if self.busy:
            raise Busy("a turn is already running; wait for it to finish")
        session_id = session_id or self.new_session()
        self._busy_session = session_id
        self.events.publish({"type": "turn_start", "session": session_id, "text": text})
        self._task = asyncio.create_task(
            self._worker(session_id, text), name=f"steward-turn-{session_id}"
        )
        return session_id

    def resolve_approval(self, approval_id: str, decision: str) -> bool:
        return self.approvals.resolve(approval_id, decision)

    # -- turn execution -------------------------------------------------

    async def _worker(self, session_id: str, text: str) -> None:
        try:
            runner = Runner(
                self.config,
                self.store,
                on_event=self._publish,
                approver=self._approve,
                provider=self._provider,
            )
            await runner.run_turn(session_id, text)
        except asyncio.CancelledError:
            self._publish({"type": "notice", "text": "cancelled"})
            raise
        except ProviderError as exc:
            self._publish({"type": "error", "text": f"provider error: {exc}"})
        except Exception as exc:  # noqa: BLE001 - the page shows what broke
            self._publish({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
        finally:
            self._busy_session = None
            self._publish({"type": "idle"})

    async def _approve(self, name: str, args: dict) -> bool:
        if name in self._always_allow:
            return True
        approval = self.approvals.create(name, args)
        self._publish({"type": "approval_request", **approval.to_dict()})
        decision = await self.approvals.wait(approval.id, APPROVAL_TIMEOUT)
        self._publish(
            {
                "type": "approval_resolved",
                "id": approval.id,
                "decision": decision or DECISION_DENY,
            }
        )
        if decision == DECISION_ALWAYS:
            self._always_allow.add(name)
        return decision in (DECISION_ONCE, DECISION_ALWAYS)

    def _publish(self, payload) -> int:
        # The runner hands us typed events; the service itself publishes dicts.
        data = payload if isinstance(payload, dict) else encode(payload)
        return self.events.publish(data)


async def fetch_model_ids(url: str, api_key: str) -> list[str]:
    """`GET {base_url}/models` with the OpenAI-compatible Bearer header.

    Gateways disagree on the response shape, so anything list-like with `id`
    members (or plain strings) is accepted; unknown shapes raise, and the
    dialog shows the message instead of an empty list pretending to be truth.
    """
    async with httpx.AsyncClient(timeout=MODELS_TIMEOUT) as client:
        response = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
    ids = parse_model_ids(response.json())
    if not ids:
        raise RuntimeError("the response did not contain a model list")
    return sorted(ids)


def parse_model_ids(payload: object) -> list[str]:
    """Accept the shapes seen in the wild: `data`/`models`, objects or strings."""
    if isinstance(payload, list):
        candidates: list[object] = payload
    elif isinstance(payload, dict):
        candidates = payload.get("data") or payload.get("models") or []
        if isinstance(candidates, dict):
            candidates = list(candidates.values())
    else:
        return []

    ids: list[str] = []
    for item in candidates:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict):
            value = item.get("id") or item.get("name")
            if isinstance(value, str):
                ids.append(value)
    return sorted(set(ids))


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
