import unittest
from typing import Any

from steward.core.context import estimate_message, fit, group_turns


def msg(role: str, content: str = "", **extra: Any) -> dict[str, Any]:
    return {"role": role, **({"content": content} if content or role != "assistant" else {}), **extra}


class TestGroupTurns(unittest.TestCase):
    def test_each_turn_starts_at_a_user_message(self) -> None:
        messages = [
            msg("user", "u1"),
            msg("assistant", "a1"),
            msg("tool", "t1", tool_call_id="c1"),
            msg("user", "u2"),
            msg("assistant", "a2"),
        ]
        turns = group_turns(messages)
        self.assertEqual([len(t) for t in turns], [3, 2])
        self.assertEqual(turns[1][0]["role"], "user")


class TestFit(unittest.TestCase):
    def _history(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for index in range(6):
            messages.append(msg("user", f"user {index} " + "x" * 400))
            messages.append(msg("assistant", f"assistant {index} " + "y" * 400))
        return messages

    def test_keeps_the_newest_turns_that_fit(self) -> None:
        messages = self._history()
        trimmed = fit(messages, budget=900)
        self.assertTrue(trimmed)
        self.assertEqual(trimmed[-1]["role"], "assistant")
        self.assertNotIn("user 0", trimmed[0].get("content", ""))
        # A turn boundary is respected: the first kept message is a user message.
        self.assertEqual(trimmed[0]["role"], "user")

    def test_system_message_is_never_dropped(self) -> None:
        messages = [msg("system", "sys")] + self._history()
        trimmed = fit(messages, budget=300)
        self.assertEqual(trimmed[0], {"role": "system", "content": "sys"})

    def test_tool_output_is_shrunk_before_turns_are_dropped(self) -> None:
        messages = [
            msg("user", "read it"),
            msg("assistant", "", tool_calls=[{
                "id": "c1",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }]),
            msg("tool", "z" * 10_000, tool_call_id="c1"),
        ]
        trimmed = fit(messages, budget=10_000, max_tool_chars=500)
        tool = next(m for m in trimmed if m["role"] == "tool")
        self.assertIn("truncated", tool["content"])
        self.assertLess(len(tool["content"]), 2_000)

    def test_estimate_is_positive_and_scales_with_content(self) -> None:
        self.assertGreater(estimate_message(msg("user", "hello")), 0)
        self.assertGreater(
            estimate_message(msg("user", "x" * 400)),
            estimate_message(msg("user", "x" * 40)),
        )


if __name__ == "__main__":
    unittest.main()
