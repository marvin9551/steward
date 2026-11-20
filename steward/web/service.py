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
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx

from steward import __version__
from steward.config import (
    PERMISSION_MODES,
    PROFILE_NAME,
    Config,
    delete_profile,
    read_overlay,
    save_overlay,
    save_profile,
    set_active_profile,
)
from steward.core.events import AgentEvent
from steward.core.runner import Runner
from steward.policy.permissions import ALIASES
from steward.providers.base import Provider, ProviderError, ProviderResult
from steward.providers.gateways import gateway_headers
from steward.providers.openai_compat import stream_chat
from steward.session.store import SessionStore
from steward.web.approvals import (
    DECISION_ALWAYS,
    DECISION_DENY,
    DECISION_ONCE,
    ApprovalRegistry,
)
from steward.web.auth import (
    MIN_PASSWORD_LENGTH,
    ensure_default_user,
    hash_password,
    load_or_create_secret,
    make_token,
    verify_password,
    verify_token,
)
from steward.web.stream import EventLog, encode

APPROVAL_TIMEOUT = 900.0
PROVIDER_FIELDS = ("base_url", "model", "api_key", "temperature", "stream_usage")
MODELS_TIMEOUT = 15.0
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_SECONDS = 30.0

# A probe is the provider seam used by `/api/provider/test`: one chat call.
ProbeFn = Callable[[Config, list[dict[str, Any]], list[dict[str, Any]]], Awaitable[ProviderResult]]
# The seam that turns `GET {base_url}/models` into model ids; tests swap it.
FetchModelsFn = Callable[[str, str], Awaitable[list[str]]]


class Busy(RuntimeError):
    """A turn is already running."""


class UnknownSession(LookupError):
    """No stored messages for this session id."""


def validate_provider_fields(
    updates: dict[str, Any], env_overrides: list[str] | None = None
) -> dict[str, Any]:
    """One place that decides what a provider edit may contain.

    `env_overrides` are fields the STEWARD_* environment pinned; a file can
    never win over them, so editing one is an explicit error.
    """
    clean: dict[str, Any] = {}
    for name in PROVIDER_FIELDS:
        if name not in updates or updates[name] is None:
            continue
        if env_overrides and name in env_overrides:
            raise ValueError(
                f"{name} is set by STEWARD_* environment and cannot be changed here"
            )
        value = updates[name]
        if name == "api_key":
            clean[name] = str(value)
        elif name == "base_url":
            text = str(value).strip()
            if not text:
                continue  # treated as "keep"
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
    return clean


class WebService:
    def __init__(
        self,
        config: Config,
        provider: Provider | None = None,
        probe: ProbeFn | None = None,
    ) -> None:
        self.config = config
        self.events = EventLog()
        self.approvals = ApprovalRegistry()
        self.store = SessionStore(config.db_path)
        self._provider = provider
        self._probe: ProbeFn = probe or stream_chat
        self._fetch_models: FetchModelsFn = fetch_model_ids
        self._task: asyncio.Task[None] | None = None
        self._busy_session: str | None = None
        self._always_allow: set[str] = set()
        self._secret = load_or_create_secret(config.data_dir)
        ensure_default_user(self.store)
        # In-process login throttling: N failures locks the username briefly.
        self._login_failures: dict[str, tuple[int, float]] = {}

    # -- auth -----------------------------------------------------------

    def verify_session(self, token: str) -> str | None:
        """Map a session token to its username, or None."""
        return verify_token(token, self._secret)

    def login(self, username: str, password: str) -> str:
        """Validate credentials and return a fresh session token.

        Failures are indistinguishable between unknown user and wrong
        password (no account enumeration), and repeated failures lock the
        username for a short while.
        """
        now = time.monotonic()
        count, locked_until = self._login_failures.get(username, (0, 0.0))
        if locked_until > now:
            raise ValueError("尝试次数过多，请稍后再试")
        user = self.store.get_user(username)
        if user is None or not verify_password(password, str(user["password_hash"])):
            count += 1
            lock = (count, now + LOGIN_LOCK_SECONDS) if count >= LOGIN_MAX_FAILURES else (count, 0.0)
            self._login_failures[username] = lock
            raise ValueError("用户名或密码不正确")
        self._login_failures.pop(username, None)
        return make_token(username, self._secret)

    def change_password(self, username: str, current: str, new: str) -> None:
        user = self.store.get_user(username)
        if user is None or not verify_password(current, str(user["password_hash"])):
            raise ValueError("当前密码不正确")
        if len(new) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"新密码至少需要 {MIN_PASSWORD_LENGTH} 个字符")
        _ = self.store.update_password(username, hash_password(new))

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

    def status(self) -> dict[str, Any]:
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

    def sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = self.store.sessions(limit=limit)
        for item in items:
            item["preview"] = _preview(item.pop("last_payload", None))
        return items

    def transcript(self, session_id: str) -> dict[str, Any]:
        """Stored messages rendered as UI blocks.

        The page and the live stream must agree on shape, so a reload after a
        turn looks exactly like having watched it.
        """
        messages: list[dict[str, Any]] = self.store.messages(session_id)
        if not messages:
            raise UnknownSession(session_id)
        return {"session": session_id, "blocks": _blocks(messages)}

    # -- writes ---------------------------------------------------------

    def new_session(self) -> str:
        return SessionStore.new_session_id()

    def set_permission(self, mode: str) -> str:
        # "ask-before-write" is a deprecated alias of "workspace-write"; keep
        # old callers working by normalizing before the mode is validated.
        mode = ALIASES.get(mode, mode)
        if mode not in PERMISSION_MODES:
            raise ValueError(f"permission must be one of {PERMISSION_MODES}, got {mode!r}")
        self.config.permission = mode
        self.events.publish({"type": "config", "permission": mode})
        return mode

    # -- provider settings ----------------------------------------------

    def provider_settings(self) -> dict[str, Any]:
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

    def set_provider(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Validate, apply, and persist page edits. Empty `api_key` means keep."""
        clean = validate_provider_fields(updates, env_overrides=self.config.env_overrides)
        if clean.get("api_key") == "":
            clean.pop("api_key")  # "keep the current key"
        for name, value in clean.items():
            setattr(self.config, name, value)
        if clean:
            save_overlay(self.config, "provider", clean)

        payload: dict[str, Any] = {"type": "provider", **self.provider_settings()}
        self.events.publish(payload)
        return self.provider_settings()

    # -- provider profiles ----------------------------------------------

    def profiles(self) -> dict[str, Any]:
        """Named provider sets saved in the overlay. Keys are never listed."""
        overlay: dict[str, Any] = read_overlay(self.config)
        section = cast("dict[str, Any] | None", overlay.get("profiles"))
        rows: list[dict[str, Any]] = []
        active: Any = None
        if isinstance(section, dict):
            active = section.get("active")
            for name, values in section.items():
                if name == "active" or not isinstance(values, dict):
                    continue
                entry = cast(dict[str, Any], values)
                rows.append(
                    {
                        "name": name,
                        "base_url": str(entry.get("base_url") or ""),
                        "model": str(entry.get("model") or ""),
                        "api_key_set": bool(entry.get("api_key")),
                        "temperature": entry.get("temperature"),
                        "stream_usage": entry.get("stream_usage"),
                    }
                )
        rows.sort(key=lambda row: str(row["name"]))
        return {"active": active, "profiles": rows}

    def save_profile(self, name: str, updates: dict[str, Any], mark_active: bool = False) -> dict[str, Any]:
        """Upsert a profile. Empty `api_key` keeps the one already in it.

        `mark_active` is what the settings dialog uses: it applies the same
        values to the active provider, so the profile it files them under is
        by definition the one in effect.
        """
        name = name.strip()
        if not PROFILE_NAME.fullmatch(name):
            raise ValueError("profile name: letters, digits, - or _ (max 32)")
        overlay: dict[str, Any] = read_overlay(self.config)
        section = cast("dict[str, Any] | None", overlay.get("profiles"))
        existing = cast("dict[str, Any] | None", section.get(name)) if isinstance(section, dict) else None

        clean = validate_provider_fields(updates)
        if clean.get("api_key") == "":
            clean.pop("api_key")  # "keep the profile's current key"
        merged = {
            key: value
            for key, value in {**dict(existing or {}), **clean}.items()
            if value is not None
        }
        save_profile(self.config, name, merged)
        if mark_active:
            set_active_profile(self.config, name)
        return self.profiles()

    def apply_profile(self, name: str) -> dict[str, Any]:
        """Copy a profile into the active provider. Absent fields keep theirs."""
        overlay: dict[str, Any] = read_overlay(self.config)
        section = cast("dict[str, Any] | None", overlay.get("profiles"))
        profile = cast("dict[str, Any] | None", section.get(name)) if isinstance(section, dict) else None
        if not isinstance(profile, dict):
            raise KeyError(name)

        clean = validate_provider_fields(profile)
        clean = {
            key: value
            for key, value in clean.items()
            if key not in self.config.env_overrides  # env beats any file
        }
        for key, value in clean.items():
            setattr(self.config, key, value)
        if clean:
            save_overlay(self.config, "provider", clean)
        set_active_profile(self.config, name)

        payload: dict[str, Any] = {"type": "provider", **self.provider_settings()}
        self.events.publish(payload)
        return self.provider_settings()

    def delete_profile(self, name: str) -> dict[str, Any]:
        if not delete_profile(self.config, name):
            raise KeyError(name)
        return self.profiles()

    async def probe_provider(self) -> dict[str, Any]:
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

    async def list_models(self, base_url: str | None = None, api_key: str | None = None) -> dict[str, Any]:
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
                on_event=self._emit_event,
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

    async def _approve(self, name: str, args: dict[str, Any]) -> bool:
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

    def _emit_event(self, event: AgentEvent) -> None:
        """Adapter for the runner's `EventSink`, which discards the sequence id."""
        _ = self._publish(event)

    def _publish(self, payload: dict[str, Any] | AgentEvent) -> int:
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
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                **gateway_headers(url),
            },
        )
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
    payload: Any = response.json()
    ids = parse_model_ids(payload)
    if not ids:
        raise RuntimeError("the response did not contain a model list")
    return sorted(ids)


def parse_model_ids(payload: object) -> list[str]:
    """Accept the shapes seen in the wild: `data`/`models`, objects or strings."""
    if isinstance(payload, list):
        candidates: list[object] = list(cast("list[object]", payload))
    elif isinstance(payload, dict):
        section = cast(dict[str, Any], payload)
        raw: Any = section.get("data") or section.get("models") or []
        if isinstance(raw, dict):
            values = cast(dict[str, Any], raw)
            candidates = list(values.values())
        else:
            candidates = list(raw)
    else:
        return []

    ids: list[str] = []
    for item in candidates:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict):
            entry = cast(dict[str, Any], item)
            value = entry.get("id") or entry.get("name")
            if isinstance(value, str):
                ids.append(value)
    return sorted(set(ids))


def _blocks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten OpenAI-style messages into the page's block list."""
    blocks: list[dict[str, Any]] = []
    by_call: dict[str, dict[str, Any]] = {}

    for message in messages:
        role = message.get("role")
        content = str(message.get("content") or "")

        if role == "user":
            blocks.append({"kind": "user", "text": content})
        elif role == "assistant":
            if content.strip():
                blocks.append({"kind": "assistant", "text": content})
            tool_calls = cast("list[object]", message.get("tool_calls") or [])
            for call in tool_calls:
                entry = cast(dict[str, Any], call)
                function = cast("dict[str, Any] | None", entry.get("function") or None)
                block: dict[str, Any] = {
                    "kind": "tool",
                    "call_id": entry.get("id") or "",
                    "name": (function or {}).get("name") or "",
                    "args": _parse_args((function or {}).get("arguments")),
                    "output": "",
                    "ok": True,
                }
                blocks.append(block)
                if block["call_id"]:
                    by_call[block["call_id"]] = block
        elif role == "tool":
            found = by_call.get(message.get("tool_call_id") or "")
            if found is None:
                new_block: dict[str, Any] = {
                    "kind": "tool",
                    "call_id": message.get("tool_call_id") or "",
                    "name": "",
                    "args": {},
                    "output": "",
                    "ok": True,
                }
                blocks.append(new_block)
                found = new_block
            found["output"] = content
            found["ok"] = not content.startswith("ERROR:")

    return blocks


def _parse_args(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return cast(dict[str, Any], raw)
    try:
        value: Any = json.loads(str(raw or "{}"))
    except ValueError:
        return {"_raw": str(raw)}
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return {"_raw": value}


def _preview(raw: object, limit: int = 60) -> str:
    if not raw:
        return ""
    try:
        payload: Any = json.loads(str(raw))
        text = str(payload.get("content") or "")
    except ValueError:
        text = str(raw)
    return " ".join(text.split())[:limit]
