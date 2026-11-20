import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from steward.config import Config
from steward.core.events import AgentEvent, Notice, ToolCallEvent, ToolResultEvent, TurnDone
from steward.core.runner import Runner
from steward.providers.base import Provider, ProviderResult, TextCallback
from steward.session.store import SessionStore


class FakeProvider:
    """Scripted provider: returns one prepared result per model call."""

    def __init__(self, results: list[ProviderResult]) -> None:
        self.results = list(results)
        self.calls: list[list[dict[str, Any]]] = []

    async def __call__(
        self,
        config: Any,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        session_id: str = "",
        on_text: TextCallback = None,
        on_reasoning: TextCallback = None,
    ) -> ProviderResult:
        self.calls.append(messages)
        result = self.results.pop(0)
        if on_text and result.text:
            on_text(result.text)
        return result


def tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> ProviderResult:
    return ProviderResult(tool_calls=[{
        "id": call_id,
        "name": name,
        "arguments": json.dumps(args),
    }])


class RunnerTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = Config(
            workspace=self.tmp / "workspace",
            data_dir=self.tmp / "data",
            model="fake",
            permission="workspace-write",
            auto_approve=True,
            max_steps=5,
        )
        self.config.workspace.mkdir(parents=True)
        self.config.data_dir.mkdir(parents=True)
        self.store = SessionStore(self.config.db_path)
        self.session_id = "test-session"

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def make_runner(
        self,
        provider: Provider,
        **kwargs: Any,
    ) -> tuple[Runner, list[AgentEvent]]:
        events: list[AgentEvent] = []
        runner = Runner(self.config, self.store, on_event=events.append, provider=provider, **kwargs)
        return runner, events


class TestRunnerLoop(RunnerTestCase):
    async def test_executes_a_tool_then_answers(self) -> None:
        provider = FakeProvider([
            tool_call("write_file", {"path": "a.txt", "content": "hi"}),
            ProviderResult(text="done", usage={"prompt_tokens": 5, "completion_tokens": 2}),
        ])
        runner, events = self.make_runner(provider)

        reply = await runner.run_turn(self.session_id, "create a.txt")

        self.assertEqual(reply, "done")
        self.assertEqual((self.config.workspace / "a.txt").read_text(), "hi")
        self.assertEqual(
            [m["role"] for m in self.store.messages(self.session_id)],
            ["user", "assistant", "tool", "assistant"],
        )
        self.assertTrue(any(isinstance(e, ToolCallEvent) for e in events))
        self.assertTrue(any(isinstance(e, ToolResultEvent) and e.ok for e in events))
        done = [e for e in events if isinstance(e, TurnDone)][0]
        self.assertEqual(done.steps, 2)
        self.assertEqual(done.usage["prompt_tokens"], 5)

    async def test_tool_result_is_replayed_to_the_model(self) -> None:
        provider = FakeProvider([
            tool_call("write_file", {"path": "a.txt", "content": "hi"}),
            ProviderResult(text="done"),
        ])
        runner, _ = self.make_runner(provider)
        await runner.run_turn(self.session_id, "create a.txt")

        second_call = provider.calls[1]
        tool_messages = [m for m in second_call if m["role"] == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0]["tool_call_id"], "call_1")
        self.assertIn("wrote", tool_messages[0]["content"])
        assistant = [m for m in second_call if m["role"] == "assistant"][0]
        self.assertEqual(assistant["tool_calls"][0]["function"]["name"], "write_file")

    async def test_read_only_mode_denies_writes(self) -> None:
        self.config.permission = "read-only"
        provider = FakeProvider([
            tool_call("write_file", {"path": "a.txt", "content": "hi"}),
            ProviderResult(text="could not"),
        ])
        runner, _ = self.make_runner(provider)

        await runner.run_turn(self.session_id, "create a.txt")

        self.assertFalse((self.config.workspace / "a.txt").exists())
        tool = [m for m in self.store.messages(self.session_id) if m["role"] == "tool"][0]
        self.assertIn("denied", tool["content"])

    async def test_approval_denial_blocks_the_tool(self) -> None:
        self.config.auto_approve = False
        provider = FakeProvider([
            tool_call("run_shell", {"command": "touch blocked.txt"}),
            ProviderResult(text="ok"),
        ])
        calls: list[tuple[str, dict[str, Any]]] = []

        async def deny(name: str, args: dict[str, Any]) -> bool:
            calls.append((name, args))
            return False

        runner, _ = self.make_runner(provider, approver=deny)
        await runner.run_turn(self.session_id, "run it")

        self.assertEqual(calls[0][0], "run_shell")
        self.assertFalse((self.config.workspace / "blocked.txt").exists())
        tool = [m for m in self.store.messages(self.session_id) if m["role"] == "tool"][0]
        self.assertIn("denied", tool["content"])

    async def test_invalid_json_arguments_become_a_tool_error(self) -> None:
        provider = FakeProvider([
            ProviderResult(tool_calls=[{
                "id": "call_bad",
                "name": "read_file",
                "arguments": "not json",
            }]),
            ProviderResult(text="done"),
        ])
        runner, _ = self.make_runner(provider)
        await runner.run_turn(self.session_id, "read")

        tool = [m for m in self.store.messages(self.session_id) if m["role"] == "tool"][0]
        self.assertIn("not valid JSON", tool["content"])

    async def test_ask_before_write_mode_gates_file_writes(self) -> None:
        self.config.permission = "ask-before-write"
        self.config.auto_approve = False
        provider = FakeProvider([
            tool_call("write_file", {"path": "a.txt", "content": "hi"}),
            ProviderResult(text="done"),
        ])
        decisions: list[tuple[str, dict[str, Any]]] = []

        async def deny(name: str, args: dict[str, Any]) -> bool:
            decisions.append((name, args))
            return False

        runner, _ = self.make_runner(provider, approver=deny)
        await runner.run_turn(self.session_id, "create a.txt")

        self.assertEqual(decisions[0][0], "write_file")
        self.assertFalse((self.config.workspace / "a.txt").exists())
        tool = [m for m in self.store.messages(self.session_id) if m["role"] == "tool"][0]
        self.assertIn("denied", tool["content"])

    async def test_step_budget_stops_runaway_tool_calls(self) -> None:
        self.config.max_steps = 2
        provider = FakeProvider([
            tool_call("list_dir", {}),
            tool_call("list_dir", {}),
            tool_call("list_dir", {}),
        ])
        runner, events = self.make_runner(provider)

        reply = await runner.run_turn(self.session_id, "loop")

        self.assertIn("step budget", reply)
        self.assertTrue(any(isinstance(e, Notice) for e in events))


if __name__ == "__main__":
    unittest.main()
