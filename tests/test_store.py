import tempfile
import threading
import unittest
from pathlib import Path

from steward.session.store import SessionStore


class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self._tmp.name) / "steward.db")

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_messages_come_back_in_order_with_their_shape(self):
        self.store.append("s1", "user", {"content": "hello"})
        self.store.append("s1", "assistant", {"content": "", "tool_calls": [{"id": "c1"}]})
        self.store.append("s1", "tool", {"tool_call_id": "c1", "content": "output"})

        messages = self.store.messages("s1")

        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "tool"])
        self.assertEqual(messages[2]["tool_call_id"], "c1")
        self.assertEqual(messages[1]["tool_calls"][0]["id"], "c1")

    def test_sessions_are_listed_newest_first(self):
        self.store.append("older", "user", {"content": "one"})
        self.store.append("newer", "user", {"content": "two"})

        listed = [item["session_id"] for item in self.store.sessions()]

        self.assertEqual(listed, ["newer", "older"])
        self.assertEqual(self.store.latest_session(), "newer")

    def test_reads_are_safe_while_a_turn_writes(self):
        """The web UI lists sessions and fetches transcripts during a turn."""
        stop = threading.Event()
        errors: list[BaseException] = []

        def reader() -> None:
            try:
                while not stop.is_set():
                    self.store.sessions()
                    self.store.latest_session()
                    self.store.messages("s1")
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            for index in range(80):
                self.store.append("s1", "user", {"content": f"message {index}"})
        finally:
            stop.set()
            thread.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertEqual(len(self.store.messages("s1")), 80)


if __name__ == "__main__":
    unittest.main()
