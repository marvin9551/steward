from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Protocol, Self, TypeAlias, cast

import httpx

import steward.config as _config_module
import steward.web.stream as _stream_module
from steward import __version__
from steward.config import Config
from steward.core.events import (
    Notice,
    ReasoningDelta,
    TextDelta,
    ToolCallEvent,
    ToolResultEvent,
    TurnDone,
)
from steward.providers.base import ProviderError, ProviderResult
from steward.web.approvals import Approval, ApprovalRegistry
from steward.web.service import Busy, UnknownSession, WebService, parse_model_ids
from steward.web.stream import EventLog

if TYPE_CHECKING:
    from fastapi import FastAPI

try:  # the optional [web] extra: the agent loop itself does not need it
    import fastapi.testclient as _probe_client

    import steward.web.api as _probe_api

    _ = _probe_client, _probe_api
    has_web = True
except ImportError:
    has_web = False

Payload: TypeAlias = dict[str, object]
ModelsFetcher: TypeAlias = Callable[[str, str], Awaitable[list[str]]]
ProbeCallable: TypeAlias = Callable[
    [Config, list[Payload], list[Payload]], Awaitable[ProviderResult]
]


class _BatchLike(Protocol):
    """EventLog.since's return shape."""

    events: list[Payload]
    lost: bool


class _EventLogLike(Protocol):
    """The EventLog surface the tests exercise, with a typed payload."""

    def publish(self, payload: Payload) -> int: ...
    def since(self, cursor: int) -> _BatchLike: ...
    def tail(self, count: int = 100) -> list[Payload]: ...
    @property
    def cursor(self) -> int: ...


class _ApprovalRegistryLike(Protocol):
    """The ApprovalRegistry surface the tests exercise, with a typed payload."""

    def create(self, name: str, args: Payload) -> Approval: ...
    def resolve(self, approval_id: str, decision: str) -> bool: ...
    async def wait(self, approval_id: str, timeout: float = ...) -> str: ...
    def pending(self) -> list[Payload]: ...
    def cancel_all(self, decision: str = ...) -> None: ...


class _ProviderLike(Protocol):
    """The provider seam: same shape as steward.providers.base.Provider."""

    async def __call__(
        self,
        config: Config,
        messages: list[Payload],
        tools: list[Payload],
        *,
        on_text: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> ProviderResult: ...


class _WebServiceLike(Protocol):
    """The WebService surface the tests exercise.

    steward/web/service.py returns bare `dict`s upstream; the protocols here
    pin them to `Payload` so the strict checker sees real types.
    """

    config: Config
    events: _EventLogLike
    approvals: _ApprovalRegistryLike
    _fetch_models: ModelsFetcher

    async def close(self) -> None: ...
    async def wait_idle(self, timeout: float | None = None) -> bool: ...
    def status(self) -> Payload: ...
    def transcript(self, session_id: str) -> Payload: ...
    def set_permission(self, mode: str) -> str: ...
    def provider_settings(self) -> Payload: ...
    def set_provider(self, updates: Payload) -> Payload: ...
    def profiles(self) -> Payload: ...
    def save_profile(
        self, name: str, updates: Payload, mark_active: bool = False
    ) -> Payload: ...
    def apply_profile(self, name: str) -> Payload: ...
    def delete_profile(self, name: str) -> Payload: ...
    async def probe_provider(self) -> Payload: ...
    async def list_models(
        self, base_url: str | None = None, api_key: str | None = None
    ) -> Payload: ...
    async def start_turn(self, session_id: str, text: str) -> str: ...
    def resolve_approval(self, approval_id: str, decision: str) -> bool: ...


class _ClientLike(Protocol):
    """The TestClient surface the tests exercise.

    starlette's TestClient types come out as Unknown under strict checking
    (its httpx2/httpx import fallback), so the HTTP verbs are retyped here.
    """

    def get(self, url: str) -> httpx.Response: ...
    def post(self, url: str, *, json: object = ...) -> httpx.Response: ...
    def delete(self, url: str) -> httpx.Response: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...


def _encode(event: object) -> Payload:
    """encode() returns a bare dict upstream; retype the seam."""
    return cast(Payload, getattr(_stream_module, "encode")(event))  # noqa: B009


def _read_overlay(config: Config) -> Payload:
    """read_overlay() returns a bare dict upstream; retype the seam."""
    return cast(Payload, getattr(_config_module, "read_overlay")(config))  # noqa: B009


def _json(response: httpx.Response) -> Payload:
    """httpx's stubs type `.json()` as `Any`; pin the payload shape here."""
    return cast(Payload, response.json())


def _create_app(config: Config, provider: _ProviderLike) -> FastAPI:
    """create_app's `provider` parameter is unannotated upstream; retype it."""
    from steward.web import api as web_api

    create_app = cast("Callable[..., FastAPI]", getattr(web_api, "create_app"))  # noqa: B009
    return create_app(config, provider=provider)


def _override_fetch(service: _WebServiceLike, fetch: ModelsFetcher) -> None:
    """Point the models seam (WebService._fetch_models) at a stub; no HTTP."""
    service._fetch_models = fetch  # pyright: ignore[reportPrivateUsage] - 测试接缝


def _transcript_blocks(service: _WebServiceLike, session_id: str) -> list[Payload]:
    """The stored transcript rendered as payload dicts."""
    return cast(list[Payload], service.transcript(session_id)["blocks"])


def bundle_built() -> bool:
    if not has_web:
        return False
    from steward.web.api import INDEX

    return INDEX.exists()


class FakeProvider:
    """Scripted provider, same shape as tests/test_runner.py."""

    def __init__(
        self, results: list[ProviderResult], gate: asyncio.Event | None = None
    ) -> None:
        self.results = list(results)
        self.gate = gate

    async def __call__(
        self,
        config: Config,
        messages: list[Payload],
        tools: list[Payload],
        *,
        on_text: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> ProviderResult:
        _ = config, messages, tools, on_reasoning
        if self.gate is not None:
            await self.gate.wait()
        result = self.results.pop(0)
        if on_text and result.text:
            on_text(result.text)
        return result


def tool_call(name: str, args: dict[str, str]) -> ProviderResult:
    return ProviderResult(
        tool_calls=[{"id": "call_1", "name": name, "arguments": json.dumps(args)}]
    )


def make_config(root: Path) -> Config:
    config = Config(
        workspace=root / "workspace",
        data_dir=root / "data",
        model="fake",
        permission="workspace-write",
        max_steps=4,
    )
    return config.prepare()


class EncodeTests(unittest.TestCase):
    def test_every_event_has_a_wire_type(self) -> None:
        cases = {
            "text": TextDelta("hi"),
            "reasoning": ReasoningDelta("think"),
            "tool_call": ToolCallEvent("read_file", {"path": "a"}),
            "tool_result": ToolResultEvent("read_file", "body", True),
            "notice": Notice("heads up"),
            "turn_done": TurnDone("done", 2, {"prompt_tokens": 3}),
        }
        for expected, event in cases.items():
            payload = _encode(event)
            self.assertEqual(payload["type"], expected)
            _ = json.dumps(payload)  # must be JSON-ready

    def test_turn_done_carries_text_for_non_streaming_providers(self) -> None:
        payload = _encode(TurnDone("full reply", 1, {}))
        self.assertEqual(payload["text"], "full reply")
        self.assertEqual(payload["steps"], 1)

    def test_unknown_object_is_a_programming_error(self) -> None:
        with self.assertRaises(TypeError):
            _ = _encode({"type": "text"})


class EventLogTests(unittest.TestCase):
    def test_cursor_replay_returns_only_newer_events(self) -> None:
        log = cast(_EventLogLike, EventLog())
        first = log.publish({"type": "text", "text": "a"})
        second = log.publish({"type": "text", "text": "b"})

        batch = log.since(first)
        self.assertFalse(batch.lost)
        self.assertEqual([item["seq"] for item in batch.events], [second])
        self.assertEqual(log.since(second).events, [])
        self.assertEqual(log.cursor, second)

    def test_fresh_client_gets_the_whole_buffer(self) -> None:
        log = cast(_EventLogLike, EventLog())
        _ = log.publish({"type": "one"})
        _ = log.publish({"type": "two"})
        self.assertEqual(len(log.since(0).events), 2)

    def test_cursor_that_fell_off_the_buffer_reports_lost(self) -> None:
        log = cast(_EventLogLike, EventLog(capacity=2))
        for name in ("one", "two", "three", "four"):
            _ = log.publish({"type": name})

        batch = log.since(1)
        self.assertTrue(batch.lost)
        self.assertEqual([item["type"] for item in batch.events], ["three", "four"])


class ApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_unblocks_when_resolved(self) -> None:
        registry = cast(_ApprovalRegistryLike, ApprovalRegistry())
        approval = registry.create("run_shell", {"command": "ls"})

        async def answer() -> None:
            await asyncio.sleep(0.02)
            _ = registry.resolve(approval.id, "once")

        task = asyncio.create_task(answer())
        self.assertEqual(await registry.wait(approval.id, 2), "once")
        await task

    async def test_timeout_counts_as_denial(self) -> None:
        registry = cast(_ApprovalRegistryLike, ApprovalRegistry())
        approval = registry.create("run_shell", {"command": "rm -rf /"})
        self.assertEqual(await registry.wait(approval.id, 0.02), "")
        self.assertEqual(registry.pending(), [])

    async def test_resolving_twice_is_refused(self) -> None:
        registry = cast(_ApprovalRegistryLike, ApprovalRegistry())
        approval = registry.create("run_shell", {})
        self.assertTrue(registry.resolve(approval.id, "deny"))
        self.assertFalse(registry.resolve(approval.id, "once"))

    async def test_unknown_decision_is_a_client_error(self) -> None:
        registry = cast(_ApprovalRegistryLike, ApprovalRegistry())
        approval = registry.create("run_shell", {})
        with self.assertRaises(ValueError):
            _ = registry.resolve(approval.id, "maybe")

    async def test_cancel_all_releases_waiters(self) -> None:
        registry = cast(_ApprovalRegistryLike, ApprovalRegistry())
        approval = registry.create("run_shell", {})
        waiting = asyncio.create_task(registry.wait(approval.id, 5))
        await asyncio.sleep(0)
        registry.cancel_all()
        self.assertEqual(await waiting, "deny")


class ServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = make_config(Path(self._tmp.name))
        self.session_id = "test-session"
        self.service: _WebServiceLike | None = None

    async def asyncTearDown(self) -> None:
        if self.service is not None:
            await self.service.close()
        self._tmp.cleanup()

    def start(
        self, provider: _ProviderLike, probe: ProbeCallable | None = None
    ) -> _WebServiceLike:
        service = cast(_WebServiceLike, WebService(self.config, provider=provider, probe=probe))
        self.service = service
        return service

    async def wait_for_event(self, kind: str, timeout: float = 5.0) -> Payload:
        assert self.service is not None, "call start() before wait_for_event()"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for item in self.service.events.tail(200):
                if item.get("type") == kind:
                    return item
            await asyncio.sleep(0.01)
        raise AssertionError(f"event {kind!r} never arrived")


class TestWebService(ServiceTestCase):
    async def test_status_never_leaks_the_key(self) -> None:
        self.config.api_key = "sk-secret"
        service = self.start(FakeProvider([]))

        status = service.status()

        self.assertNotIn("api_key", status)
        self.assertTrue(status["has_api_key"])
        self.assertEqual(
            status["permissions"],
            ["read-only", "ask-before-write", "workspace-write", "full"],
        )
        self.assertEqual(status["version"], __version__)

    async def test_a_turn_streams_events_and_lands_in_the_transcript(self) -> None:
        service = self.start(FakeProvider([ProviderResult(text="hello there")]))

        await service.start_turn(self.session_id, "hi")
        self.assertTrue(await service.wait_idle(5))

        kinds = [item["type"] for item in service.events.tail(50)]
        self.assertEqual(kinds[0], "turn_start")
        self.assertIn("text", kinds)
        self.assertIn("turn_done", kinds)
        self.assertEqual(kinds[-1], "idle")

        blocks = _transcript_blocks(service, self.session_id)
        self.assertEqual([block["kind"] for block in blocks], ["user", "assistant"])
        self.assertEqual(blocks[1]["text"], "hello there")

    async def test_second_turn_is_refused_while_one_is_running(self) -> None:
        gate = asyncio.Event()
        service = self.start(FakeProvider([ProviderResult(text="slow")], gate=gate))

        await service.start_turn(self.session_id, "first")
        try:
            with self.assertRaises(Busy):
                await service.start_turn(self.session_id, "second")
        finally:
            gate.set()
        self.assertTrue(await service.wait_idle(5))

    async def test_tool_call_and_result_are_rendered_as_one_card(self) -> None:
        service = self.start(
            FakeProvider(
                [
                    tool_call("write_file", {"path": "a.txt", "content": "hi"}),
                    ProviderResult(text="done"),
                ]
            )
        )

        await service.start_turn(self.session_id, "create a.txt")
        request = await self.wait_for_event("approval_request")
        self.assertEqual(request["name"], "write_file")

        self.assertTrue(service.resolve_approval(cast(str, request["id"]), "once"))
        self.assertTrue(await service.wait_idle(5))

        blocks = _transcript_blocks(service, self.session_id)
        tool = [block for block in blocks if block["kind"] == "tool"]
        self.assertEqual(len(tool), 1)
        self.assertEqual(tool[0]["name"], "write_file")
        self.assertTrue(tool[0]["ok"])
        self.assertIn("wrote", cast(str, tool[0]["output"]))

    async def test_approval_card_gates_the_shell_tool(self) -> None:
        self.config.auto_approve = False
        service = self.start(
            FakeProvider(
                [
                    tool_call("run_shell", {"command": "echo approved"}),
                    ProviderResult(text="done"),
                ]
            )
        )

        await service.start_turn(self.session_id, "run it")
        request = await self.wait_for_event("approval_request")
        self.assertEqual(request["name"], "run_shell")

        self.assertTrue(service.resolve_approval(cast(str, request["id"]), "once"))
        self.assertTrue(await service.wait_idle(5))

        blocks = _transcript_blocks(service, self.session_id)
        tool = [block for block in blocks if block["kind"] == "tool"][0]
        self.assertTrue(tool["ok"])
        self.assertIn("approved", cast(str, tool["output"]))

    async def test_always_approval_is_remembered_for_the_process(self) -> None:
        self.config.auto_approve = False
        service = self.start(
            FakeProvider(
                [
                    tool_call("run_shell", {"command": "echo one"}),
                    tool_call("run_shell", {"command": "echo two"}),
                    ProviderResult(text="done"),
                ]
            )
        )

        await service.start_turn(self.session_id, "run twice")
        request = await self.wait_for_event("approval_request")
        _ = service.resolve_approval(cast(str, request["id"]), "always")
        self.assertTrue(await service.wait_idle(5))

        requests = [e for e in service.events.tail(100) if e.get("type") == "approval_request"]
        self.assertEqual(len(requests), 1)
        outputs = [
            b["output"]
            for b in _transcript_blocks(service, self.session_id)
            if b["kind"] == "tool"
        ]
        self.assertEqual(outputs, ["one", "two"])

    async def test_ask_before_write_gates_file_tools_with_a_card(self) -> None:
        self.config.auto_approve = False
        self.config.permission = "ask-before-write"
        service = self.start(
            FakeProvider(
                [
                    tool_call("write_file", {"path": "a.txt", "content": "hi"}),
                    ProviderResult(text="done"),
                ]
            )
        )

        await service.start_turn(self.session_id, "create a.txt")
        request = await self.wait_for_event("approval_request")
        self.assertEqual(request["name"], "write_file")

        _ = service.resolve_approval(cast(str, request["id"]), "once")
        self.assertTrue(await service.wait_idle(5))
        self.assertTrue((self.config.workspace / "a.txt").exists())

    async def test_denying_the_approval_blocks_execution(self) -> None:
        self.config.auto_approve = False
        service = self.start(
            FakeProvider(
                [
                    tool_call("run_shell", {"command": "touch denied.txt"}),
                    ProviderResult(text="stopped"),
                ]
            )
        )

        await service.start_turn(self.session_id, "run it")
        request = await self.wait_for_event("approval_request")
        _ = service.resolve_approval(cast(str, request["id"]), "deny")
        self.assertTrue(await service.wait_idle(5))

        self.assertFalse((self.config.workspace / "denied.txt").exists())
        blocks = _transcript_blocks(service, self.session_id)
        tool = [block for block in blocks if block["kind"] == "tool"][0]
        self.assertFalse(tool["ok"])
        self.assertIn("denied", cast(str, tool["output"]))

    async def test_transcript_of_an_unknown_session_raises(self) -> None:
        service = self.start(FakeProvider([]))
        with self.assertRaises(UnknownSession):
            _ = service.transcript("nope")

    async def test_permission_change_is_validated(self) -> None:
        service = self.start(FakeProvider([]))
        self.assertEqual(service.set_permission("full"), "full")
        self.assertEqual(self.config.permission, "full")
        self.assertEqual(service.status()["permission"], "full")
        with self.assertRaises(ValueError):
            _ = service.set_permission("god-mode")

    async def test_empty_message_is_rejected(self) -> None:
        service = self.start(FakeProvider([]))
        with self.assertRaises(ValueError):
            _ = await service.start_turn(self.session_id, "   ")


class TestProviderSettings(ServiceTestCase):
    def overlay_text(self) -> str:
        path = self.config.data_dir / "settings.toml"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    async def test_settings_hide_the_key_and_report_env_locks(self) -> None:
        self.config.api_key = "sk-secret"
        self.config.env_overrides = ["base_url", "data_dir"]
        service = self.start(FakeProvider([]))

        settings = service.provider_settings()

        self.assertTrue(settings["api_key_set"])
        self.assertNotIn("api_key", settings)
        self.assertEqual(settings["env_overrides"], ["base_url"])

    async def test_set_provider_applies_and_persists_to_the_overlay(self) -> None:
        service = self.start(FakeProvider([]))

        _ = service.set_provider(
            {
                "model": "page-model",
                "base_url": "https://api.test/v1/",
                "stream_usage": True,
                "temperature": 0.7,
                "api_key": "sk-from-page",
            }
        )

        self.assertEqual(self.config.model, "page-model")
        self.assertEqual(self.config.base_url, "https://api.test/v1")
        self.assertTrue(self.config.stream_usage)
        text = self.overlay_text()
        self.assertIn('model = "page-model"', text)
        self.assertIn('api_key = "sk-from-page"', text)
        self.assertIn("stream_usage = true", text)
        published = [e for e in service.events.tail(20) if e.get("type") == "provider"]
        self.assertEqual(published[0]["model"], "page-model")
        self.assertNotIn("api_key", published[0])

    async def test_empty_api_key_keeps_the_current_one(self) -> None:
        self.config.api_key = "sk-kept"
        service = self.start(FakeProvider([]))

        _ = service.set_provider({"api_key": "", "model": "page-model"})

        self.assertEqual(self.config.api_key, "sk-kept")
        self.assertNotIn("api_key", self.overlay_text())

    async def test_env_locked_fields_are_refused(self) -> None:
        self.config.env_overrides = ["api_key", "model"]
        service = self.start(FakeProvider([]))

        with self.assertRaises(ValueError):
            _ = service.set_provider({"api_key": "sk-x"})
        with self.assertRaises(ValueError):
            _ = service.set_provider({"model": "nope"})

    async def test_invalid_values_are_rejected(self) -> None:
        service = self.start(FakeProvider([]))
        bad_cases: tuple[Payload, ...] = (
            {"base_url": "ftp://api.test"},
            {"model": "   "},
            {"temperature": 9},
            {"temperature": "hot"},
        )
        for bad in bad_cases:
            with self.assertRaises(ValueError, msg=repr(bad)):
                _ = service.set_provider(bad)

        # Blank base_url means "keep", like a blank key.
        before = self.config.base_url
        _ = service.set_provider({"base_url": "   "})
        self.assertEqual(self.config.base_url, before)

    async def test_probe_reports_success_and_provider_errors(self) -> None:
        async def good(
            _config: Config, _messages: list[Payload], _tools: list[Payload]
        ) -> ProviderResult:
            return ProviderResult(text="pong")

        async def broken(
            _config: Config, _messages: list[Payload], _tools: list[Payload]
        ) -> ProviderResult:
            raise ProviderError("HTTP 401: bad key")

        healthy = self.start(FakeProvider([]), probe=good)
        failing = self.start(FakeProvider([]), probe=broken)

        self.assertEqual(await healthy.probe_provider(), {"ok": True, "reply": "pong"})
        failure = await failing.probe_provider()
        self.assertFalse(failure["ok"])
        self.assertIn("401", cast(str, failure["error"]))

    async def test_list_models_uses_saved_and_unsaved_values(self) -> None:
        self.config.api_key = "sk-saved"
        seen: list[tuple[str, str]] = []

        async def fetch(url: str, api_key: str) -> list[str]:
            seen.append((url, api_key))
            return ["glm-5.3", "deepseek-chat"]

        service = self.start(FakeProvider([]))
        _override_fetch(service, fetch)

        # No overrides: the saved config decides. The seam returns the list as
        # given; sorting is fetch_model_ids' job.
        result = await service.list_models()
        self.assertEqual(result["models"], ["glm-5.3", "deepseek-chat"])
        self.assertTrue(seen[-1][0].endswith("/models"))
        self.assertEqual(seen[-1][1], "sk-saved")

        # Unsaved form values win, so sync works before the first save.
        result = await service.list_models("https://gateway.internal/v1", "form-key")
        self.assertTrue(seen[-1][0].startswith("https://gateway.internal/v1/models"))
        self.assertEqual(seen[-1][1], "form-key")
        self.assertEqual(len(cast(list[str], result["models"])), 2)

    async def test_list_models_needs_a_key_and_a_url(self) -> None:
        service = self.start(FakeProvider([]))

        no_key = await service.list_models()
        self.assertIn("api_key is empty", cast(str, no_key["error"]))

        self.config.api_key = "sk-x"
        bad_url = await service.list_models("ftp://nope")
        self.assertIn("http://", cast(str, bad_url["error"]))

    async def test_list_models_reports_gateway_errors(self) -> None:
        async def fetch(_url: str, _api_key: str) -> list[str]:
            raise RuntimeError("HTTP 502: bad gateway")

        service = self.start(FakeProvider([]))
        _override_fetch(service, fetch)
        self.config.api_key = "sk-x"

        result = await service.list_models()
        self.assertEqual(result["models"], [])
        self.assertIn("502", cast(str, result["error"]))

    async def test_env_locked_values_are_ignored_by_sync(self) -> None:
        self.config.api_key = "sk-saved"
        self.config.env_overrides = ["base_url", "api_key"]
        seen: list[tuple[str, str]] = []

        async def fetch(url: str, api_key: str) -> list[str]:
            seen.append((url, api_key))
            return ["m1"]

        service = self.start(FakeProvider([]))
        _override_fetch(service, fetch)

        _ = await service.list_models("https://form-url/v1", "form-key")

        self.assertNotIn("form-url", seen[-1][0])
        self.assertEqual(seen[-1][1], "sk-saved")


class TestProfiles(ServiceTestCase):
    def overlay(self) -> Payload:
        return _read_overlay(self.config)

    async def test_save_list_apply_delete_round_trip(self) -> None:
        service = self.start(FakeProvider([]))
        self.assertEqual(service.profiles(), {"active": None, "profiles": []})

        listing = service.save_profile(
            "opencode-go",
            {"base_url": "https://go.internal/v1", "model": "glm-5.3", "api_key": "sk-go"},
        )
        rows = cast(list[Payload], listing["profiles"])
        self.assertEqual([row["name"] for row in rows], ["opencode-go"])
        self.assertTrue(rows[0]["api_key_set"])
        self.assertNotIn("api_key", rows[0])

        # An empty key keeps the one already stored in the profile.
        _ = service.save_profile("opencode-go", {"api_key": "", "model": "glm-5.4"})
        rows = cast(list[Payload], service.profiles()["profiles"])
        row = rows[0]
        self.assertEqual(row["model"], "glm-5.4")
        self.assertTrue(row["api_key_set"])

        # The settings dialog marks the profile it files under as current.
        listing = service.save_profile(
            "deepseek", {"base_url": "https://api.deepseek.com/v1"}, mark_active=True
        )
        self.assertEqual(listing["active"], "deepseek")
        self.assertEqual(len(cast(list[Payload], listing["profiles"])), 2)

        # Apply: absent fields keep their current values.
        self.config.temperature = 1.5
        settings = service.apply_profile("opencode-go")
        self.assertEqual(settings["base_url"], "https://go.internal/v1")
        self.assertEqual(settings["model"], "glm-5.4")
        self.assertEqual(settings["temperature"], 1.5)
        self.assertEqual(service.profiles()["active"], "opencode-go")
        self.assertEqual(self.config.api_key, "sk-go")  # applied but never listed

    async def test_apply_never_overrides_env_locked_fields(self) -> None:
        self.config.env_overrides = ["base_url"]
        self.config.base_url = "https://env-url/v1"
        service = self.start(FakeProvider([]))
        _ = service.save_profile("p1", {"base_url": "https://file-url/v1", "model": "m"})

        _ = service.apply_profile("p1")

        self.assertEqual(self.config.base_url, "https://env-url/v1")
        self.assertEqual(self.config.model, "m")

    async def test_missing_profiles_raise_key_errors(self) -> None:
        service = self.start(FakeProvider([]))
        with self.assertRaises(KeyError):
            _ = service.apply_profile("ghost")
        with self.assertRaises(KeyError):
            _ = service.delete_profile("ghost")

    async def test_profile_names_are_validated(self) -> None:
        service = self.start(FakeProvider([]))
        for bad in ("", "   ", "has space", "x" * 40, "删除!"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                _ = service.save_profile(bad, {"model": "m"})


class TestParseModelIds(unittest.TestCase):
    def test_accepts_the_shapes_gateways_actually_return(self) -> None:
        cases = [
            ({"data": [{"id": "a"}, {"id": "b"}]}, ["a", "b"]),
            ({"data": ["a", "b"]}, ["a", "b"]),
            ({"models": [{"id": "a"}, {"name": "b"}]}, ["a", "b"]),
            (["a", "b"], ["a", "b"]),
            ({"data": [{"id": "a"}, {"id": "a"}]}, ["a"]),
        ]
        for payload, expected in cases:
            self.assertEqual(parse_model_ids(payload), expected, msg=repr(payload))

    def test_garbage_is_empty_not_an_error(self) -> None:
        self.assertEqual(parse_model_ids({"error": "x"}), [])
        self.assertEqual(parse_model_ids(None), [])
        self.assertEqual(parse_model_ids("text"), [])


@unittest.skipUnless(has_web, "install the [web] extra: pip install -e '.[web]'")
class TestStreamEvents(ServiceTestCase):
    async def collect(
        self, service: _WebServiceLike, cursor: int, iterations: int = 1
    ) -> list[str]:
        calls = {"count": 0}

        async def stop() -> bool:
            calls["count"] += 1
            return calls["count"] > iterations

        from steward.web.api import stream_events

        return [
            chunk
            async for chunk in stream_events(
                cast(WebService, service), cursor, stop=stop, poll=0
            )
        ]

    async def test_replays_only_events_after_the_cursor(self) -> None:
        service = self.start(FakeProvider([]))
        _ = service.events.publish({"type": "text", "text": "a"})
        second = service.events.publish({"type": "text", "text": "b"})

        chunks = await self.collect(service, cursor=1)

        self.assertEqual(len(chunks), 1)
        self.assertIn(f"id: {second}", chunks[0])
        self.assertIn('"text": "b"', chunks[0])

    async def test_stale_cursor_asks_the_page_to_resync(self) -> None:
        service = self.start(FakeProvider([]))
        service.events = cast(_EventLogLike, EventLog(capacity=2))
        for name in ("one", "two", "three", "four"):
            _ = service.events.publish({"type": name})

        chunks = await self.collect(service, cursor=1)

        self.assertIn('"type": "resync"', chunks[0])
        # After a resync we continue from now, not from the stale cursor.
        self.assertEqual(len(chunks), 1)


@unittest.skipUnless(bundle_built(), "needs the [web] extra and a built frontend bundle")
class TestHttpSurface(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = make_config(Path(self._tmp.name))
        self.provider = FakeProvider([ProviderResult(text="hi")])

        # First start creates the default account; its random password only
        # exists here, so log in once and let the client keep the cookie.
        from steward.session.store import SessionStore
        from steward.web.auth import ensure_default_user

        store = SessionStore(self.config.db_path)
        created = ensure_default_user(store)
        assert created is not None
        self.default_password = created[1]
        store.close()

        self.app = _create_app(self.config, provider=self.provider)

        from fastapi.testclient import TestClient

        self.client = cast(_ClientLike, TestClient(self.app))
        _ = self.client.__enter__()
        response = self.client.post(
            "/api/login",
            json={"username": "admin", "password": self.default_password},
        )
        assert response.status_code == 200

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self._tmp.cleanup()

    @property
    def service(self) -> _WebServiceLike:
        return cast(_WebServiceLike, self.app.state.service)

    def wait_idle(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = _json(self.client.get("/api/status"))
            if not cast(bool, status["busy"]):
                return
            time.sleep(0.02)
        raise AssertionError("turn never finished")

    def test_index_serves_the_built_page(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="root"', response.text)

    def test_status_endpoint_is_json(self) -> None:
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_json(response)["model"], "fake")

    def test_unknown_api_route_is_a_404_with_an_error_field(self) -> None:
        response = self.client.get("/api/nope")
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", _json(response))

    def test_unknown_post_route_is_a_404_not_a_405(self) -> None:
        # A stale process serving a newer page produces exactly this request.
        response = self.client.post("/api/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertIn("no route for", cast(str, _json(response)["error"]))

    def test_spa_fallback_serves_index_for_unknown_paths(self) -> None:
        response = self.client.get("/some/deep/link")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="root"', response.text)

    def test_turn_endpoint_starts_a_turn_and_stores_it(self) -> None:
        response = self.client.post("/api/turn", json={"session": None, "text": "hi"})
        self.assertEqual(response.status_code, 200)
        session_id = _json(response)["session_id"]
        self.wait_idle()

        transcript = _json(self.client.get(f"/api/sessions/{session_id}"))
        blocks = cast(list[Payload], transcript["blocks"])
        self.assertEqual([b["kind"] for b in blocks], ["user", "assistant"])

    def test_empty_turn_is_a_400(self) -> None:
        response = self.client.post("/api/turn", json={"text": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", _json(response))

    def test_malformed_body_is_a_400_with_an_error_field(self) -> None:
        response = self.client.post("/api/turn", json={"session": "s"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", _json(response))

    def test_missing_transcript_is_a_404(self) -> None:
        self.assertEqual(self.client.get("/api/sessions/nope").status_code, 404)

    def test_invalid_permission_is_a_400(self) -> None:
        response = self.client.post("/api/permission", json={"mode": "god-mode"})
        self.assertEqual(response.status_code, 400)

    def test_provider_settings_round_trip(self) -> None:
        current = _json(self.client.get("/api/provider"))
        self.assertEqual(current["model"], "fake")
        self.assertNotIn("api_key", current)

        response = self.client.post(
            "/api/provider", json={"model": "page-model", "api_key": ""}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_json(response)["model"], "page-model")
        self.assertEqual(self.config.model, "page-model")
        overlay = self.config.data_dir / "settings.toml"
        self.assertIn('model = "page-model"', overlay.read_text(encoding="utf-8"))

    def test_invalid_provider_update_is_a_400(self) -> None:
        response = self.client.post("/api/provider", json={"temperature": 9})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", _json(response))

    def test_provider_test_reports_a_missing_key_without_network(self) -> None:
        response = self.client.post("/api/provider/test")
        self.assertEqual(response.status_code, 200)
        payload = _json(response)
        self.assertFalse(payload["ok"])
        self.assertIn("api_key", cast(str, payload["error"]))

    def test_models_sync_reports_a_missing_key_without_network(self) -> None:
        response = self.client.post("/api/models", json={})
        self.assertEqual(response.status_code, 200)
        payload = _json(response)
        self.assertEqual(payload["models"], [])
        self.assertIn("api_key", cast(str, payload["error"]))

    def test_profile_endpoints_round_trip(self) -> None:
        created = self.client.post(
            "/api/profiles",
            json={
                "name": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
                "api_key": "sk-d",
            },
        )
        self.assertEqual(created.status_code, 200)
        listed = _json(self.client.get("/api/profiles"))
        rows = cast(list[Payload], listed["profiles"])
        self.assertEqual(rows[0]["name"], "deepseek")
        self.assertNotIn("api_key", rows[0])

        applied = self.client.post("/api/profiles/apply", json={"name": "deepseek"})
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(_json(applied)["model"], "deepseek-chat")
        self.assertEqual(
            _json(self.client.get("/api/provider"))["model"], "deepseek-chat"
        )

        self.assertEqual(
            self.client.post("/api/profiles/apply", json={"name": "ghost"}).status_code,
            404,
        )
        self.assertEqual(self.client.delete("/api/profiles/deepseek").status_code, 200)
        self.assertEqual(self.client.delete("/api/profiles/deepseek").status_code, 404)
        self.assertEqual(
            self.client.post("/api/profiles", json={"name": "bad name!"}).status_code,
            400,
        )

    def test_permission_change_is_visible_in_status(self) -> None:
        _ = self.client.post("/api/permission", json={"mode": "read-only"})
        self.assertEqual(
            _json(self.client.get("/api/status"))["permission"], "read-only"
        )

    def test_unknown_approval_is_a_404(self) -> None:
        response = self.client.post("/api/approval", json={"id": "nope", "decision": "once"})
        self.assertEqual(response.status_code, 404)

    def test_approval_over_http_unblocks_the_turn(self) -> None:
        self.config.auto_approve = False
        self.provider.results = [
            tool_call("run_shell", {"command": "echo over-http"}),
            ProviderResult(text="done"),
        ]
        _ = self.client.post("/api/turn", json={"session": "s-http", "text": "run it"})

        deadline = time.monotonic() + 5
        pending: list[Payload] = []
        while time.monotonic() < deadline and not pending:
            pending = self.service.approvals.pending()
            time.sleep(0.02)
        self.assertTrue(pending, "approval card never appeared")

        response = self.client.post(
            "/api/approval", json={"id": pending[0]["id"], "decision": "once"}
        )
        self.assertEqual(response.status_code, 200)
        self.wait_idle()

        transcript = _json(self.client.get("/api/sessions/s-http"))
        blocks = cast(list[Payload], transcript["blocks"])
        tool = [b for b in blocks if b["kind"] == "tool"][0]
        self.assertTrue(tool["ok"])
        self.assertIn("over-http", cast(str, tool["output"]))


if __name__ == "__main__":
    unittest.main()
