import os
import tempfile
import unittest
from pathlib import Path

from steward.config import (
    Config,
    apply_stored,
    load,
    migrate_files,
    persist_stored,
)
from steward.policy.permissions import ALLOW, ASK, DENY, decide
from steward.session.store import SessionStore


class TestConfig(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._env_names = (
            "STEWARD_API_KEY",
            "STEWARD_MODEL",
            "STEWARD_BASE_URL",
            "STEWARD_DATA_DIR",
            "STEWARD_WORKSPACE",
            "STEWARD_PERMISSION",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()
        for name in self._env_names:
            os.environ.pop(name, None)

    def make_store(self, config: Config) -> SessionStore:
        return SessionStore(config.db_path)

    def test_env_values_apply_and_report_the_pin(self) -> None:
        os.environ["STEWARD_API_KEY"] = "from-env"
        os.environ["STEWARD_BASE_URL"] = "https://example.test/v1"

        config = load()

        self.assertEqual(config.api_key, "from-env")
        self.assertEqual(config.base_url, "https://example.test/v1")
        self.assertIn("api_key", config.env_overrides)
        self.assertIn("base_url", config.env_overrides)

    def test_unknown_permission_is_rejected(self) -> None:
        os.environ["STEWARD_PERMISSION"] = "yolo"
        with self.assertRaises(ValueError):
            load()

    def test_stored_settings_apply_to_the_config(self) -> None:
        config = load()
        store = self.make_store(config)
        _ = store.set_settings(
            {
                "base_url": '"https://go.test/v1"',
                "model": '"stored-model"',
                "temperature": "0.9",
                "stream_usage": "true",
            }
        )

        apply_stored(config, store)

        self.assertEqual(config.base_url, "https://go.test/v1")
        self.assertEqual(config.model, "stored-model")
        self.assertEqual(config.temperature, 0.9)
        self.assertTrue(config.stream_usage)

    def test_stored_values_lose_to_env_pins(self) -> None:
        os.environ["STEWARD_MODEL"] = "env-model"
        config = load()
        store = self.make_store(config)
        _ = store.set_settings({"model": '"stored-model"'})

        apply_stored(config, store)

        self.assertEqual(config.model, "env-model")

    def test_persist_and_apply_round_trip(self) -> None:
        config = load()
        store = self.make_store(config)

        persist_stored(config, store, {"model": "next-model", "api_key": "sk-next"})
        fresh = load()
        apply_stored(fresh, store)

        self.assertEqual(fresh.model, "next-model")
        self.assertEqual(fresh.api_key, "sk-next")

    def test_corrupted_rows_are_skipped(self) -> None:
        config = load()
        store = self.make_store(config)
        _ = store.set_settings({"model": '"good"', "temperature": "not-a-number"})

        apply_stored(config, store)

        self.assertEqual(config.model, "good")
        self.assertEqual(config.temperature, 0.3)  # default survives

    def test_migrate_files_imports_the_old_config_once(self) -> None:
        old_cwd = Path.cwd()
        os.chdir(self.tmp)
        try:
            os.environ["STEWARD_DATA_DIR"] = str(self.tmp / "data")
            config = load()
            store = self.make_store(config)

            (self.tmp / "config.toml").write_text(
                """
[provider]
base_url = "https://old.example/v1"
model = "old-model"

[profiles.opencode-go]
base_url = "https://go.test/v1"
model = "glm-5.3"
""",
                encoding="utf-8",
            )
            config.data_dir.mkdir(parents=True, exist_ok=True)
            (config.data_dir / "settings.toml").write_text(
                """
[provider]
api_key = "overlay-key"

[profiles]
active = "opencode-go"
""",
                encoding="utf-8",
            )

            migrate_files(config, store)
        finally:
            os.chdir(old_cwd)
        fresh = load()
        apply_stored(fresh, store)

        self.assertEqual(fresh.base_url, "https://old.example/v1")
        self.assertEqual(fresh.model, "old-model")
        self.assertEqual(fresh.api_key, "overlay-key")
        saved = store.profiles()
        self.assertEqual([row["name"] for row in saved], ["opencode-go"])
        self.assertEqual(store.settings().get("active_profile"), '"opencode-go"')

        # Once the DB holds keys, stale files can no longer overwrite them.
        persist_stored(config, store, {"model": "page-model"})
        migrate_files(config, store)
        again = load()
        apply_stored(again, store)
        self.assertEqual(again.model, "page-model")


class TestPermissionTable(unittest.TestCase):
    def test_read_only(self) -> None:
        self.assertEqual(decide("read-only", "read_file"), ALLOW)
        self.assertEqual(decide("read-only", "write_file"), DENY)
        self.assertEqual(decide("read-only", "run_shell"), DENY)

    def test_ask_before_write_is_a_deprecated_alias(self) -> None:
        self.assertEqual(decide("ask-before-write", "read_file"), ALLOW)
        self.assertEqual(decide("ask-before-write", "write_file"), ASK)
        self.assertEqual(decide("ask-before-write", "run_shell"), ASK)

    def test_workspace_write_asks_for_every_mutation(self) -> None:
        self.assertEqual(decide("workspace-write", "read_file"), ALLOW)
        self.assertEqual(decide("workspace-write", "list_dir"), ALLOW)
        self.assertEqual(decide("workspace-write", "write_file"), ASK)
        self.assertEqual(decide("workspace-write", "edit_file"), ASK)
        self.assertEqual(decide("workspace-write", "run_shell"), ASK)

    def test_full(self) -> None:
        self.assertEqual(decide("full", "run_shell"), ALLOW)

if __name__ == "__main__":
    unittest.main()
