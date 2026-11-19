import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

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
from steward.web.approvals import ApprovalRegistry
from steward.web.service import Busy, UnknownSession, WebService
from steward.web.stream import EventLog, encode

try:  # the optional [web] extra: the agent loop itself does not need it
    from fastapi.testclient import TestClient

    from steward.web import api as web_api
    from steward.web.api import create_app, stream_events
except ImportError:
    HAS_WEB = False
else:
    HAS_WEB = True


def bundle_built() -> bool:
    return HAS_WEB and web_api.INDEX.exists()


class FakeProvider:
    """Scripted provider, same shape as tests/test_runner.py."""

    def __init__(self, results, gate: asyncio.Event | None = None):
        self.results = list(results)
        self.gate = gate

    async def __call__(self, config, messages, tools, *, on_text=None, on_reasoning=None):
        if self.gate is not None:
            await self.gate.wait()
        result = self.results.pop(0)
        if on_text and result.text:
            on_text(result.text)
        return result


def tool_call(name: str, args: dict) -> ProviderResult:
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
    def test_every_event_has_a_wire_type(self):
        cases = {
            "text": TextDelta("hi"),
            "reasoning": ReasoningDelta("think"),
            "tool_call": ToolCallEvent("read_file", {"path": "a"}),
            "tool_result": ToolResultEvent("read_file", "body", True),
            "notice": Notice("heads up"),
            "turn_done": TurnDone("done", 2, {"prompt_tokens": 3}),
        }
        for expected, event in cases.items():
            payload = encode(event)
            self.assertEqual(payload["type"], expected)
            json.dumps(payload)  # must be JSON-ready

    def test_turn_done_carries_text_for_non_streaming_providers(self):
        payload = encode(TurnDone("full reply", 1, {}))
        self.assertEqual(payload["text"], "full reply")
        self.assertEqual(payload["steps"], 1)

    def test_unknown_object_is_a_programming_error(self):
        with self.assertRaises(TypeError):
            encode({"type": "text"})


class EventLogTests(unittest.TestCase):
    def test_cursor_replay_returns_only_newer_events(self):
        log = EventLog()
        first = log.publish({"type": "text", "text": "a"})
        second = log.publish({"type": "text", "text": "b"})

        batch = log.since(first)
        self.assertFalse(batch.lost)
        self.assertEqual([item["seq"] for item in batch.events], [second])
        self.assertEqual(log.since(second).events, [])
        self.assertEqual(log.cursor, second)

    def test_fresh_client_gets_the_whole_buffer(self):
        log = EventLog()
        log.publish({"type": "one"})
        log.publish({"type": "two"})
        self.assertEqual(len(log.since(0).events), 2)

    def test_cursor_that_fell_off_the_buffer_reports_lost(self):
        log = EventLog(capacity=2)
        for name in ("one", "two", "three", "four"):
            log.publish({"type": name})

        batch = log.since(1)
        self.assertTrue(batch.lost)
        self.assertEqual([item["type"] for item in batch.events], ["three", "four"])


class ApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_unblocks_when_resolved(self):
        registry = ApprovalRegistry()
        approval = registry.create("run_shell", {"command": "ls"})

        async def answer():
            await asyncio.sleep(0.02)
            registry.resolve(approval.id, "once")

        task = asyncio.create_task(answer())
        self.assertEqual(await registry.wait(approval.id, 2), "once")
        await task

    async def test_timeout_counts_as_denial(self):
        registry = ApprovalRegistry()
        approval = registry.create("run_shell", {"command": "rm -rf /"})
        self.assertEqual(await registry.wait(approval.id, 0.02), "")
        self.assertEqual(registry.pending(), [])

    async def test_resolving_twice_is_refused(self):
        registry = ApprovalRegistry()
        approval = registry.create("run_shell", {})
        self.assertTrue(registry.resolve(approval.id, "deny"))
        self.assertFalse(registry.resolve(approval.id, "once"))

    async def test_unknown_decision_is_a_client_error(self):
        registry = ApprovalRegistry()
        approval = registry.create("run_shell", {})
        with self.assertRaises(ValueError):
            registry.resolve(approval.id, "maybe")

    async def test_cancel_all_releases_waiters(self):
        registry = ApprovalRegistry()
        approval = registry.create("run_shell", {})
        waiting = asyncio.create_task(registry.wait(approval.id, 5))
        await asyncio.sleep(0)
        registry.cancel_all()
        self.assertEqual(await waiting, "deny")


class ServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config = make_config(Path(self._tmp.name))
        self.session_id = "test-session"
        self.service: WebService | None = None

    async def asyncTearDown(self):
        if self.service is not None:
            await self.service.close()
        self._tmp.cleanup()

    def start(self, provider, probe=None) -> WebService:
        self.service = WebService(self.config, provider=provider, probe=probe)
        return self.service

    async def wait_for_event(self, kind: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for item in self.service.events.tail(200):
                if item.get("type") == kind:
                    return item
            await asyncio.sleep(0.01)
        raise AssertionError(f"event {kind!r} never arrived")


class TestWebService(ServiceTestCase):
    async def test_status_never_leaks_the_key(self):
        self.config.api_key = "sk-secret"
        service = self.start(FakeProvider([]))

        status = service.status()

        self.assertNotIn("api_key", status)
        self.assertTrue(status["has_api_key"])
        self.assertEqual(status["permissions"], ["read-only", "workspace-write", "full"])
        self.assertEqual(status["version"], __version__)

    async def test_a_turn_streams_events_and_lands_in_the_transcript(self):
        service = self.start(FakeProvider([ProviderResult(text="hello there")]))

        await service.start_turn(self.session_id, "hi")
        self.assertTrue(await service.wait_idle(5))

        kinds = [item["type"] for item in service.events.tail(50)]
        self.assertEqual(kinds[0], "turn_start")
        self.assertIn("text", kinds)
        self.assertIn("turn_done", kinds)
        self.assertEqual(kinds[-1], "idle")

        blocks = service.transcript(self.session_id)["blocks"]
        self.assertEqual([block["kind"] for block in blocks], ["user", "assistant"])
        self.assertEqual(blocks[1]["text"], "hello there")

    async def test_second_turn_is_refused_while_one_is_running(self):
        gate = asyncio.Event()
        service = self.start(FakeProvider([ProviderResult(text="slow")], gate=gate))

        await service.start_turn(self.session_id, "first")
        try:
            with self.assertRaises(Busy):
                await service.start_turn(self.session_id, "second")
        finally:
            gate.set()
        self.assertTrue(await service.wait_idle(5))

    async def test_tool_call_and_result_are_rendered_as_one_card(self):
        service = self.start(
            FakeProvider(
                [
                    tool_call("write_file", {"path": "a.txt", "content": "hi"}),
                    ProviderResult(text="done"),
                ]
            )
        )

        await service.start_turn(self.session_id, "create a.txt")
        self.assertTrue(await service.wait_idle(5))

        blocks = service.transcript(self.session_id)["blocks"]
        tool = [block for block in blocks if block["kind"] == "tool"]
        self.assertEqual(len(tool), 1)
        self.assertEqual(tool[0]["name"], "write_file")
        self.assertTrue(tool[0]["ok"])
        self.assertIn("wrote", tool[0]["output"])

    async def test_approval_card_gates_the_shell_tool(self):
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

        self.assertTrue(service.resolve_approval(request["id"], "once"))
        self.assertTrue(await service.wait_idle(5))

        blocks = service.transcript(self.session_id)["blocks"]
        tool = [block for block in blocks if block["kind"] == "tool"][0]
        self.assertTrue(tool["ok"])
        self.assertIn("approved", tool["output"])

    async def test_always_approval_is_remembered_for_the_process(self):
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
        service.resolve_approval(request["id"], "always")
        self.assertTrue(await service.wait_idle(5))

        requests = [e for e in service.events.tail(100) if e["type"] == "approval_request"]
        self.assertEqual(len(requests), 1)
        outputs = [b["output"] for b in service.transcript(self.session_id)["blocks"] if b["kind"] == "tool"]
        self.assertEqual(outputs, ["one", "two"])

    async def test_denying_the_approval_blocks_execution(self):
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
        service.resolve_approval(request["id"], "deny")
        self.assertTrue(await service.wait_idle(5))

        self.assertFalse((self.config.workspace / "denied.txt").exists())
        blocks = service.transcript(self.session_id)["blocks"]
        tool = [block for block in blocks if block["kind"] == "tool"][0]
        self.assertFalse(tool["ok"])
        self.assertIn("denied", tool["output"])

    async def test_transcript_of_an_unknown_session_raises(self):
        service = self.start(FakeProvider([]))
        with self.assertRaises(UnknownSession):
            service.transcript("nope")

    async def test_permission_change_is_validated(self):
        service = self.start(FakeProvider([]))
        self.assertEqual(service.set_permission("full"), "full")
        self.assertEqual(self.config.permission, "full")
        self.assertEqual(service.status()["permission"], "full")
        with self.assertRaises(ValueError):
            service.set_permission("god-mode")

    async def test_empty_message_is_rejected(self):
        service = self.start(FakeProvider([]))
        with self.assertRaises(ValueError):
            await service.start_turn(self.session_id, "   ")


class TestProviderSettings(ServiceTestCase):
    def overlay_text(self) -> str:
        path = self.config.data_dir / "settings.toml"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    async def test_settings_hide_the_key_and_report_env_locks(self):
        self.config.api_key = "sk-secret"
        self.config.env_overrides = ["base_url", "data_dir"]
        service = self.start(FakeProvider([]))

        settings = service.provider_settings()

        self.assertTrue(settings["api_key_set"])
        self.assertNotIn("api_key", settings)
        self.assertEqual(settings["env_overrides"], ["base_url"])

    async def test_set_provider_applies_and_persists_to_the_overlay(self):
        service = self.start(FakeProvider([]))

        service.set_provider(
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

    async def test_empty_api_key_keeps_the_current_one(self):
        self.config.api_key = "sk-kept"
        service = self.start(FakeProvider([]))

        service.set_provider({"api_key": "", "model": "page-model"})

        self.assertEqual(self.config.api_key, "sk-kept")
        self.assertNotIn("api_key", self.overlay_text())

    async def test_env_locked_fields_are_refused(self):
        self.config.env_overrides = ["api_key", "model"]
        service = self.start(FakeProvider([]))

        with self.assertRaises(ValueError):
            service.set_provider({"api_key": "sk-x"})
        with self.assertRaises(ValueError):
            service.set_provider({"model": "nope"})

    async def test_invalid_values_are_rejected(self):
        service = self.start(FakeProvider([]))
        for bad in (
            {"base_url": "ftp://api.test"},
            {"base_url": "   "},
            {"model": "   "},
            {"temperature": 9},
            {"temperature": "hot"},
        ):
            with self.assertRaises(ValueError, msg=repr(bad)):
                service.set_provider(bad)

    async def test_probe_reports_success_and_provider_errors(self):
        async def good(config, messages, tools, *, on_text=None, on_reasoning=None):
            return ProviderResult(text="pong")

        async def broken(config, messages, tools, *, on_text=None, on_reasoning=None):
            raise ProviderError("HTTP 401: bad key")

        healthy = self.start(FakeProvider([]), probe=good)
        failing = self.start(FakeProvider([]), probe=broken)

        self.assertEqual(await healthy.probe_provider(), {"ok": True, "reply": "pong"})
        failure = await failing.probe_provider()
        self.assertFalse(failure["ok"])
        self.assertIn("401", failure["error"])


@unittest.skipUnless(HAS_WEB, "install the [web] extra: pip install -e '.[web]'")
class TestStreamEvents(ServiceTestCase):
    async def collect(self, service, cursor: int, iterations: int = 1) -> list[str]:
        calls = {"count": 0}

        async def stop() -> bool:
            calls["count"] += 1
            return calls["count"] > iterations

        return [chunk async for chunk in stream_events(service, cursor, stop=stop, poll=0)]

    async def test_replays_only_events_after_the_cursor(self):
        service = self.start(FakeProvider([]))
        service.events.publish({"type": "text", "text": "a"})
        second = service.events.publish({"type": "text", "text": "b"})

        chunks = await self.collect(service, cursor=1)

        self.assertEqual(len(chunks), 1)
        self.assertIn(f"id: {second}", chunks[0])
        self.assertIn('"text": "b"', chunks[0])

    async def test_stale_cursor_asks_the_page_to_resync(self):
        service = self.start(FakeProvider([]))
        service.events = EventLog(capacity=2)
        for name in ("one", "two", "three", "four"):
            service.events.publish({"type": name})

        chunks = await self.collect(service, cursor=1)

        self.assertIn('"type": "resync"', chunks[0])
        # After a resync we continue from now, not from the stale cursor.
        self.assertEqual(len(chunks), 1)


@unittest.skipUnless(bundle_built(), "needs the [web] extra and a built frontend bundle")
class TestHttpSurface(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config = make_config(Path(self._tmp.name))
        self.provider = FakeProvider([ProviderResult(text="hi")])
        self.app = create_app(self.config, provider=self.provider)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self._tmp.cleanup()

    @property
    def service(self) -> WebService:
        return self.app.state.service

    def wait_idle(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.client.get("/api/status").json()["busy"]:
                return
            time.sleep(0.02)
        raise AssertionError("turn never finished")

    def test_index_serves_the_built_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="root"', response.text)

    def test_status_endpoint_is_json(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["model"], "fake")

    def test_unknown_api_route_is_a_404_with_an_error_field(self):
        response = self.client.get("/api/nope")
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.json())

    def test_spa_fallback_serves_index_for_unknown_paths(self):
        response = self.client.get("/some/deep/link")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="root"', response.text)

    def test_turn_endpoint_starts_a_turn_and_stores_it(self):
        response = self.client.post("/api/turn", json={"session": None, "text": "hi"})
        self.assertEqual(response.status_code, 200)
        session_id = response.json()["session_id"]
        self.wait_idle()

        transcript = self.client.get(f"/api/sessions/{session_id}").json()
        self.assertEqual([b["kind"] for b in transcript["blocks"]], ["user", "assistant"])

    def test_empty_turn_is_a_400(self):
        response = self.client.post("/api/turn", json={"text": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_malformed_body_is_a_400_with_an_error_field(self):
        response = self.client.post("/api/turn", json={"session": "s"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_missing_transcript_is_a_404(self):
        self.assertEqual(self.client.get("/api/sessions/nope").status_code, 404)

    def test_invalid_permission_is_a_400(self):
        response = self.client.post("/api/permission", json={"mode": "god-mode"})
        self.assertEqual(response.status_code, 400)

    def test_provider_settings_round_trip(self):
        current = self.client.get("/api/provider").json()
        self.assertEqual(current["model"], "fake")
        self.assertNotIn("api_key", current)

        response = self.client.post(
            "/api/provider", json={"model": "page-model", "api_key": ""}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["model"], "page-model")
        self.assertEqual(self.config.model, "page-model")
        overlay = self.config.data_dir / "settings.toml"
        self.assertIn('model = "page-model"', overlay.read_text(encoding="utf-8"))

    def test_invalid_provider_update_is_a_400(self):
        response = self.client.post("/api/provider", json={"temperature": 9})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_provider_test_reports_a_missing_key_without_network(self):
        response = self.client.post("/api/provider/test")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertIn("api_key", payload["error"])

    def test_permission_change_is_visible_in_status(self):
        self.client.post("/api/permission", json={"mode": "read-only"})
        self.assertEqual(self.client.get("/api/status").json()["permission"], "read-only")

    def test_unknown_approval_is_a_404(self):
        response = self.client.post("/api/approval", json={"id": "nope", "decision": "once"})
        self.assertEqual(response.status_code, 404)

    def test_approval_over_http_unblocks_the_turn(self):
        self.config.auto_approve = False
        self.provider.results = [
            tool_call("run_shell", {"command": "echo over-http"}),
            ProviderResult(text="done"),
        ]
        self.client.post("/api/turn", json={"session": "s-http", "text": "run it"})

        deadline = time.monotonic() + 5
        pending = []
        while time.monotonic() < deadline and not pending:
            pending = self.service.approvals.pending()
            time.sleep(0.02)
        self.assertTrue(pending, "approval card never appeared")

        response = self.client.post(
            "/api/approval", json={"id": pending[0]["id"], "decision": "once"}
        )
        self.assertEqual(response.status_code, 200)
        self.wait_idle()

        blocks = self.client.get("/api/sessions/s-http").json()["blocks"]
        tool = [b for b in blocks if b["kind"] == "tool"][0]
        self.assertTrue(tool["ok"])
        self.assertIn("over-http", tool["output"])


if __name__ == "__main__":
    unittest.main()
