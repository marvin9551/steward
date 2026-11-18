import tempfile
import unittest
from pathlib import Path

from steward.tools import execute
from steward.tools.base import ToolContext


class TestFileTools(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self.ctx = ToolContext(workspace=self.workspace)

    def tearDown(self):
        self._tmp.cleanup()

    async def test_write_read_edit_roundtrip(self):
        out = await execute(
            "write_file",
            {"path": "notes/a.txt", "content": "hello\nworld\n"},
            self.ctx,
        )
        self.assertIn("wrote", out)

        out = await execute("read_file", {"path": "notes/a.txt"}, self.ctx)
        self.assertIn("1\thello", out)
        self.assertIn("2\tworld", out)

        out = await execute(
            "edit_file", {"path": "notes/a.txt", "old": "world", "new": "agent"}, self.ctx
        )
        self.assertIn("replaced 1", out)
        self.assertEqual((self.workspace / "notes/a.txt").read_text(), "hello\nagent\n")

    async def test_edit_requires_a_unique_match(self):
        await execute("write_file", {"path": "x.txt", "content": "a a"}, self.ctx)
        out = await execute("edit_file", {"path": "x.txt", "old": "a", "new": "b"}, self.ctx)
        self.assertTrue(out.startswith("ERROR:"))
        self.assertIn("2 times", out)

    async def test_paths_outside_the_workspace_are_refused(self):
        out = await execute("read_file", {"path": "../outside.txt"}, self.ctx)
        self.assertTrue(out.startswith("ERROR:"))
        self.assertIn("outside the workspace", out)

    async def test_read_pages_large_files(self):
        content = "\n".join(f"line {i}" for i in range(1, 51))
        await execute("write_file", {"path": "big.txt", "content": content}, self.ctx)
        out = await execute("read_file", {"path": "big.txt", "offset": 10, "limit": 5}, self.ctx)
        self.assertIn("10\tline 10", out)
        self.assertIn("14\tline 14", out)
        self.assertNotIn("15\tline 15", out)

    async def test_unknown_tool_lists_available_tools(self):
        out = await execute("nope", {}, self.ctx)
        self.assertIn("unknown tool", out)
        self.assertIn("read_file", out)


class TestShellTool(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self.ctx = ToolContext(workspace=self.workspace)

    def tearDown(self):
        self._tmp.cleanup()

    async def test_runs_in_the_workspace(self):
        out = await execute("run_shell", {"command": "pwd"}, self.ctx)
        self.assertEqual(out.strip(), str(self.workspace.resolve()))

    async def test_nonzero_exit_is_reported(self):
        out = await execute("run_shell", {"command": "exit 3"}, self.ctx)
        self.assertIn("[exit 3]", out)

    async def test_output_is_truncated_by_the_executor(self):
        self.ctx.max_output = 200
        out = await execute(
            "run_shell",
            {"command": "python3 -c \"print('x' * 5000)\""},
            self.ctx,
        )
        self.assertIn("truncated", out)

    async def test_shell_does_not_inherit_secrets(self):
        import os

        os.environ["STEWARD_TEST_SECRET"] = "leak-me"
        try:
            out = await execute(
                "run_shell", {"command": "echo ${STEWARD_TEST_SECRET:-missing}"}, self.ctx
            )
        finally:
            os.environ.pop("STEWARD_TEST_SECRET", None)
        self.assertEqual(out.strip(), "missing")


if __name__ == "__main__":
    unittest.main()
