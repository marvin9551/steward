import os
import tempfile
import unittest
from pathlib import Path

from steward.config import load
from steward.policy.permissions import ALLOW, ASK, DENY, decide


class TestConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("STEWARD_API_KEY", None)

    def test_toml_values_and_env_override(self):
        (self.tmp / "config.toml").write_text(
            f"""
[provider]
base_url = "https://example.test/v1"
model = "some-model"
api_key = "from-file"

[agent]
workspace = "{self.tmp / 'ws'}"
data_dir = "{self.tmp / 'data'}"
permission = "read-only"
max_steps = 7
""",
            encoding="utf-8",
        )
        os.environ["STEWARD_API_KEY"] = "from-env"

        config = load(self.tmp / "config.toml")

        self.assertEqual(config.api_key, "from-env")
        self.assertEqual(config.model, "some-model")
        self.assertEqual(config.permission, "read-only")
        self.assertEqual(config.max_steps, 7)
        self.assertTrue(config.workspace.is_dir())
        self.assertTrue(config.data_dir.is_dir())

    def test_unknown_permission_is_rejected(self):
        (self.tmp / "config.toml").write_text(
            f"""
[agent]
workspace = "{self.tmp / 'ws'}"
data_dir = "{self.tmp / 'data'}"
permission = "yolo"
""",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            load(self.tmp / "config.toml")


class TestPermissionTable(unittest.TestCase):
    def test_read_only(self):
        self.assertEqual(decide("read-only", "read_file"), ALLOW)
        self.assertEqual(decide("read-only", "write_file"), DENY)
        self.assertEqual(decide("read-only", "run_shell"), DENY)

    def test_workspace_write(self):
        self.assertEqual(decide("workspace-write", "write_file"), ALLOW)
        self.assertEqual(decide("workspace-write", "run_shell"), ASK)

    def test_full(self):
        self.assertEqual(decide("full", "run_shell"), ALLOW)


if __name__ == "__main__":
    unittest.main()
