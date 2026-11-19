import json
import unittest
from typing import Any

from steward.providers.openai_compat import StreamAccumulator


def chunk(**delta: Any) -> dict[str, Any]:
    return {"choices": [{"delta": delta}]}


class TestStreamAccumulator(unittest.TestCase):
    def test_text_chunks_accumulate(self) -> None:
        acc = StreamAccumulator()
        acc.feed(chunk(content="Hel"))
        acc.feed(chunk(content="lo"))
        result = acc.result()
        self.assertEqual(result.text, "Hello")
        self.assertEqual(result.tool_calls, [])

    def test_reasoning_is_kept_separate_from_text(self) -> None:
        acc = StreamAccumulator()
        acc.feed(chunk(reasoning_content="think "))
        acc.feed(chunk(reasoning_content="hard", content="answer"))
        result = acc.result()
        self.assertEqual(result.reasoning, "think hard")
        self.assertEqual(result.text, "answer")

    def test_tool_call_arguments_split_across_chunks(self) -> None:
        acc = StreamAccumulator()
        acc.feed({"choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "id": "call_1",
            "function": {"name": "read_file", "arguments": '{"pa'},
        }]}}]})
        acc.feed({"choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "function": {"arguments": 'th": "notes.txt"}'},
        }]}}]})
        result = acc.result()
        self.assertEqual(len(result.tool_calls), 1)
        call = result.tool_calls[0]
        self.assertEqual(call["id"], "call_1")
        self.assertEqual(call["name"], "read_file")
        self.assertEqual(json.loads(call["arguments"]), {"path": "notes.txt"})

    def test_parallel_tool_calls_are_ordered_by_index(self) -> None:
        acc = StreamAccumulator()
        for index, name in ((1, "b"), (0, "a")):
            acc.feed({"choices": [{"delta": {"tool_calls": [{
                "index": index,
                "id": f"call_{name}",
                "function": {"name": name, "arguments": "{}"},
            }]}}]})
        self.assertEqual([c["name"] for c in acc.result().tool_calls], ["a", "b"])

    def test_usage_chunk_without_choices(self) -> None:
        acc = StreamAccumulator()
        acc.feed({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 3}})
        self.assertEqual(acc.result().usage["prompt_tokens"], 10)


if __name__ == "__main__":
    unittest.main()
