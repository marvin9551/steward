import os
import tempfile
import unittest
from pathlib import Path

from steward.config import Config, load, save_overlay
from steward.policy.permissions import ALLOW, ASK, DENY, decide


class TestConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        for name in (
            "STEWARD_API_KEY",
            "STEWARD_MODEL",
            "STEWARD_DATA_DIR",
            "STEWARD_WORKSPACE",
        ):
            os.environ.pop(name, None)

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

    def test_page_overlay_applies_and_env_still_wins(self):
        data = self.tmp / "data"
        data.mkdir()
        save_overlay(Config(data_dir=data), "provider", {
            "model": "overlay-model",
            "api_key": "overlay-key",
        })
        os.environ["STEWARD_DATA_DIR"] = str(data)
        os.environ["STEWARD_WORKSPACE"] = str(self.tmp / "ws")

        config = load(self.tmp / "missing.toml")

        self.assertEqual(config.model, "overlay-model")
        self.assertEqual(config.api_key, "overlay-key")
        self.assertIn("data_dir", config.env_overrides)

        os.environ["STEWARD_MODEL"] = "env-model"
        config = load(self.tmp / "missing.toml")
        self.assertEqual(config.model, "env-model")
        self.assertEqual(config.api_key, "overlay-key")

    def test_save_overlay_merges_and_can_drop_a_key(self):
        data = self.tmp / "data"
        data.mkdir()
        config = Config(data_dir=data)
        path = save_overlay(config, "provider", {"model": "one", "temperature": 0.9})

        save_overlay(config, "provider", {"model": None, "api_key": "sk-new"})
        text = path.read_text(encoding="utf-8")

        self.assertNotIn("model =", text)
        self.assertIn('api_key = "sk-new"', text)
        self.assertIn("temperature = 0.9", text)
        self.assertIn("Managed by the steward web UI", text)


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
