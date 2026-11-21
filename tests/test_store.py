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

    def test_messages_come_back_in_order_with_their_shape(self) -> None:
        self.store.append("s1", "user", {"content": "hello"})
        self.store.append("s1", "assistant", {"content": "", "tool_calls": [{"id": "c1"}]})
        self.store.append("s1", "tool", {"tool_call_id": "c1", "content": "output"})

        messages = self.store.messages("s1")

        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "tool"])
        self.assertEqual(messages[2]["tool_call_id"], "c1")
        self.assertEqual(messages[1]["tool_calls"][0]["id"], "c1")

    def test_sessions_are_listed_newest_first(self) -> None:
        self.store.append("older", "user", {"content": "one"})
        self.store.append("newer", "user", {"content": "two"})

        listed = [item["session_id"] for item in self.store.sessions()]

        self.assertEqual(listed, ["newer", "older"])
        self.assertEqual(self.store.latest_session(), "newer")

    def test_reads_are_safe_while_a_turn_writes(self) -> None:
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

    def test_append_and_query_logs_with_filters_and_redaction(self) -> None:
        id1 = self.store.append_log(
            "INFO",
            "agent",
            "turn.start",
            "开始对话",
            session_id="s-1",
            detail={"model": "glm-5", "api_key": "sk-secret", "nested": {"app_secret": "sec-1"}},
        )
        id2 = self.store.append_log(
            "ERROR",
            "tool",
            "tool.result",
            "run_shell 执行失败",
            session_id="s-1",
            detail={"name": "run_shell", "ok": False},
        )
        id3 = self.store.append_log(
            "WARN",
            "system",
            "auth.login",
            "登录失败",
            detail={"username": "admin", "password": "pw"},
        )

        self.assertLess(id1, id2)
        self.assertLess(id2, id3)

        all_res = self.store.query_logs()
        self.assertFalse(all_res["has_more"])
        logs = all_res["logs"]
        self.assertEqual([item["id"] for item in logs], [id3, id2, id1])

        # Verify sensitive fields are redacted
        self.assertEqual(logs[0]["detail"]["password"], "***")
        self.assertEqual(logs[2]["detail"]["api_key"], "***")
        self.assertEqual(logs[2]["detail"]["nested"]["app_secret"], "***")

        # Filter by level
        err_res = self.store.query_logs(level="ERROR")
        self.assertEqual([item["id"] for item in err_res["logs"]], [id2])

        # Filter by category
        cat_res = self.store.query_logs(category="agent")
        self.assertEqual([item["id"] for item in cat_res["logs"]], [id1])

        # Filter by session_id
        sess_res = self.store.query_logs(session_id="s-1")
        self.assertEqual([item["id"] for item in sess_res["logs"]], [id2, id1])

        # Filter by keyword query
        q_res = self.store.query_logs(query="run_shell")
        self.assertEqual([item["id"] for item in q_res["logs"]], [id2])

        # Cursor pagination with limit and before_id
        page1 = self.store.query_logs(limit=2)
        self.assertTrue(page1["has_more"])
        self.assertEqual([item["id"] for item in page1["logs"]], [id3, id2])

        page2 = self.store.query_logs(limit=2, before_id=id2)
        self.assertFalse(page2["has_more"])
        self.assertEqual([item["id"] for item in page2["logs"]], [id1])

        # Clear logs
        cleared = self.store.clear_logs()
        self.assertEqual(cleared, 3)
        self.assertEqual(self.store.query_logs()["logs"], [])


if __name__ == "__main__":
    unittest.main()
