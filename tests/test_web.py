import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
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
from steward.providers.base import ProviderResult
from steward.web import server as web_server
from steward.web.approvals import ApprovalRegistry
from steward.web.service import Busy, UnknownSession, WebService
from steward.web.stream import EventLog, encode


class FakeProvider:
    """Scripted provider, same shape as tests/test_runner.py."""

    def __init__(self, results, gate=None):
        self.results = list(results)
        self.gate = gate

    async def __call__(self, config, messages, tools, *, on_text=None, on_reasoning=None):
        if self.gate is not None:
            self.gate.wait(2)
        result = self.results.pop(0)
        if on_text and result.text:
            on_text(result.text)
        return result


def tool_call(name: str, args: dict) -> ProviderResult:
    return ProviderResult(
        tool_calls=[{"id": "call_1", "name": name, "arguments": json.dumps(args)}]
    )


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
        log.publish({"type": "one"})
        log.publish({"type": "two"})
        log.publish({"type": "three"})
        log.publish({"type": "four"})

        batch = log.since(1)
        self.assertTrue(batch.lost)
        self.assertEqual([item["type"] for item in batch.events], ["three", "four"])


class ApprovalTests(unittest.TestCase):
    def test_wait_unblocks_when_resolved(self):
        registry = ApprovalRegistry()
        approval = registry.create("run_shell", {"command": "ls"})
        threading.Timer(0.05, registry.resolve, args=(approval.id, "once")).start()

        self.assertEqual(registry.wait(approval.id, 2), "once")

    def test_timeout_counts_as_denial(self):
        registry = ApprovalRegistry()
        approval = registry.create("run_shell", {"command": "rm -rf /"})
        self.assertEqual(registry.wait(approval.id, 0.05), "")
        self.assertEqual(registry.pending(), [])

    def test_resolving_twice_is_refused(self):
        registry = ApprovalRegistry()
        approval = registry.create("run_shell", {})
        self.assertTrue(registry.resolve(approval.id, "deny"))
        self.assertFalse(registry.resolve(approval.id, "once"))

    def test_unknown_decision_is_a_client_error(self):
        registry = ApprovalRegistry()
        approval = registry.create("run_shell", {})
        with self.assertRaises(ValueError):
            registry.resolve(approval.id, "maybe")


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config = Config(
            workspace=self.root / "workspace",
            data_dir=self.root / "data",
            model="fake",
            permission="workspace-write",
            max_steps=4,
        )
        self.config.prepare()
        self.store_session = "test-session"
        self.service = None

    def tearDown(self):
        if self.service is not None:
            self.service.close()
        self._tmp.cleanup()

    def start(self, provider):
        self.service = WebService(self.config, provider=provider)
        return self.service

    def wait_for_event(self, kind: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for item in self.service.events.tail(200):
                if item.get("type") == kind:
                    return item
            time.sleep(0.02)
        raise AssertionError(f"event {kind!r} never arrived")


class TestWebService(ServiceTestCase):
    def test_status_never_leaks_the_key(self):
        self.config.api_key = "sk-secret"
        service = self.start(FakeProvider([]))

        status = service.status()

        self.assertNotIn("api_key", status)
        self.assertTrue(status["has_api_key"])
        self.assertEqual(status["permissions"], ["read-only", "workspace-write", "full"])
        self.assertEqual(status["version"], __version__)

    def test_a_turn_streams_events_and_lands_in_the_transcript(self):
        service = self.start(FakeProvider([ProviderResult(text="hello there")]))

        service.start_turn(self.store_session, "hi")
        self.assertTrue(service.wait_idle(5))

        kinds = [item["type"] for item in service.events.tail(50)]
        self.assertEqual(kinds[0], "turn_start")
        self.assertIn("text", kinds)
        self.assertIn("turn_done", kinds)
        self.assertEqual(kinds[-1], "idle")

        blocks = service.transcript(self.store_session)["blocks"]
        self.assertEqual([block["kind"] for block in blocks], ["user", "assistant"])
        self.assertEqual(blocks[1]["text"], "hello there")

    def test_second_turn_is_refused_while_one_is_running(self):
        gate = threading.Event()
        service = self.start(FakeProvider([ProviderResult(text="slow")], gate=gate))

        service.start_turn(self.store_session, "first")
        try:
            with self.assertRaises(Busy):
                service.start_turn(self.store_session, "second")
        finally:
            gate.set()
        self.assertTrue(service.wait_idle(5))

    def test_tool_call_and_result_are_rendered_as_one_card(self):
        service = self.start(
            FakeProvider(
                [
                    tool_call("write_file", {"path": "a.txt", "content": "hi"}),
                    ProviderResult(text="done"),
                ]
            )
        )

        service.start_turn(self.store_session, "create a.txt")
        self.assertTrue(service.wait_idle(5))

        blocks = service.transcript(self.store_session)["blocks"]
        tool = [block for block in blocks if block["kind"] == "tool"]
        self.assertEqual(len(tool), 1)
        self.assertEqual(tool[0]["name"], "write_file")
        self.assertTrue(tool[0]["ok"])
        self.assertIn("wrote", tool[0]["output"])

    def test_approval_card_gates_the_shell_tool(self):
        self.config.auto_approve = False
        service = self.start(
            FakeProvider(
                [
                    tool_call("run_shell", {"command": "echo approved"}),
                    ProviderResult(text="done"),
                ]
            )
        )

        service.start_turn(self.store_session, "run it")
        request = self.wait_for_event("approval_request")
        self.assertEqual(request["name"], "run_shell")

        self.assertTrue(service.resolve_approval(request["id"], "once"))
        self.assertTrue(service.wait_idle(5))

        blocks = service.transcript(self.store_session)["blocks"]
        tool = [block for block in blocks if block["kind"] == "tool"][0]
        self.assertTrue(tool["ok"])
        self.assertIn("approved", tool["output"])

    def test_denying_the_approval_blocks_execution(self):
        self.config.auto_approve = False
        service = self.start(
            FakeProvider(
                [
                    tool_call("run_shell", {"command": "touch denied.txt"}),
                    ProviderResult(text="stopped"),
                ]
            )
        )

        service.start_turn(self.store_session, "run it")
        request = self.wait_for_event("approval_request")
        service.resolve_approval(request["id"], "deny")
        self.assertTrue(service.wait_idle(5))

        self.assertFalse((self.config.workspace / "denied.txt").exists())
        blocks = service.transcript(self.store_session)["blocks"]
        tool = [block for block in blocks if block["kind"] == "tool"][0]
        self.assertFalse(tool["ok"])
        self.assertIn("denied", tool["output"])

    def test_transcript_of_an_unknown_session_raises(self):
        service = self.start(FakeProvider([]))
        with self.assertRaises(UnknownSession):
            service.transcript("nope")

    def test_permission_change_is_validated(self):
        service = self.start(FakeProvider([]))
        self.assertEqual(service.set_permission("full"), "full")
        self.assertEqual(self.config.permission, "full")
        self.assertEqual(service.status()["permission"], "full")
        with self.assertRaises(ValueError):
            service.set_permission("god-mode")

    def test_empty_message_is_rejected(self):
        service = self.start(FakeProvider([]))
        with self.assertRaises(ValueError):
            service.start_turn(self.store_session, "   ")


@unittest.skipUnless(web_server.INDEX.exists(), "frontend bundle is not built")
class TestHttpSurface(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.server, self.service = web_server.bind(
            self.config,
            "127.0.0.1",
            0,
            attempts=1,
            quiet=True,
            provider=FakeProvider([ProviderResult(text="hi")]),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        super().tearDown()

    def request(self, path: str, method: str = "GET", payload: dict | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"{self.base}{path}",
            method=method,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def test_index_serves_the_built_page(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn('id="root"', body)

    def test_status_endpoint_is_json(self):
        status, body = self.request("/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["model"], "fake")

    def test_unknown_api_route_is_a_404(self):
        status, _ = self.request("/api/nope")
        self.assertEqual(status, 404)

    def test_spa_fallback_serves_index_for_unknown_paths(self):
        status, body = self.request("/some/deep/link")
        self.assertEqual(status, 200)
        self.assertIn('id="root"', body)

    def test_turn_endpoint_starts_a_turn(self):
        status, body = self.request("/api/turn", "POST", {"session": None, "text": "hi"})
        self.assertEqual(status, 200)
        session_id = json.loads(body)["session_id"]
        self.assertTrue(session_id)
        self.assertTrue(self.service.wait_idle(5))

    def test_missing_transcript_is_a_404(self):
        status, _ = self.request("/api/sessions/does-not-exist")
        self.assertEqual(status, 404)

    def test_invalid_permission_is_a_400(self):
        status, _ = self.request("/api/permission", "POST", {"mode": "god-mode"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
