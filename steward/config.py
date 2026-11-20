"""Configuration: one Config object, stored in SQLite.

Precedence, weakest to strongest:

1. dataclass defaults
2. the `settings` table in `steward.db` (the web UI writes here)
3. `STEWARD_*` environment variables (bootstrap + deliberate pinning)

There is no config file any more: every value the page can edit lives in
SQLite, so what the UI shows is exactly what is stored — and what is stored
is exactly what the UI shows. Only the database *location* is configured
outside it (`STEWARD_DATA_DIR`), because it decides where the database is.

A fresh deployment that still carries the old `config.toml` /
`<data_dir>/settings.toml` is migrated once (see `migrate_files`), then the
files are simply ignored.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from steward.session.store import SessionStore

PERMISSION_MODES = ("read-only", "workspace-write", "full")

# Config attrs persisted in the settings table; the page may edit all of them.
STORED_KEYS: tuple[str, ...] = (
    "base_url",
    "model",
    "api_key",
    "temperature",
    "stream_usage",
    "permission",
)

ENV_MAP = {
    "STEWARD_API_KEY": "api_key",
    "STEWARD_BASE_URL": "base_url",
    "STEWARD_MODEL": "model",
    "STEWARD_WORKSPACE": "workspace",
    "STEWARD_DATA_DIR": "data_dir",
    "STEWARD_PERMISSION": "permission",
}


def _expand(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser()


@dataclass
class Config:
    # Provider
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.3
    timeout: float = 600.0
    stream_usage: bool = False

    # Agent
    workspace: Path = field(default_factory=lambda: _expand("~/.steward/workspace"))
    data_dir: Path = field(default_factory=lambda: _expand("~/.steward"))
    max_steps: int = 25
    context_budget: int = 60_000
    max_tool_output: int = 20_000
    permission: str = "workspace-write"
    auto_approve: bool = False
    show_reasoning: bool = False
    # Fields the environment set during load(); the web UI refuses to edit
    # these because an env var would win on the next restart.
    env_overrides: list[str] = field(default_factory=list)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "steward.db"

    def prepare(self) -> "Config":
        """Create the data/workspace directories and validate the mode."""
        if self.permission == "ask-before-write":
            self.permission = "workspace-write"  # deprecated alias, same table
        if self.permission not in PERMISSION_MODES:
            raise ValueError(
                f"permission must be one of {PERMISSION_MODES}, got {self.permission!r}"
            )
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self


def _coerce(name: str, value: Any, current: object) -> object:
    """Convert a stored/env value to the dataclass field's type."""
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, Path):
        return _expand(str(value))
    return str(value)


def load() -> Config:
    """Assemble the config: defaults, then STEWARD_* environment variables.

    Everything else lives in the settings table and is applied by
    `apply_stored` once a store is open. Environment wins over the database so
    a deployment can pin a value (the page then locks that field).
    """
    cfg = Config()

    for env_name, attr in ENV_MAP.items():
        value = os.environ.get(env_name)
        if value:
            cfg.env_overrides.append(attr)
            setattr(cfg, attr, _coerce(attr, value, getattr(cfg, attr)))

    return cfg.prepare()


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _decode(name: str, raw: str, current: object) -> object:
    return _coerce(name, json.loads(raw), current)


def apply_stored(config: Config, store: SessionStore) -> None:
    """Overlay the settings table onto the config. Env-pinned fields win."""
    values = store.settings()
    for key in STORED_KEYS:
        if key not in values or key in config.env_overrides:
            continue
        try:
            setattr(config, key, _decode(key, values[key], getattr(config, key)))
        except (TypeError, ValueError):
            continue  # a corrupted row must not take the whole service down


def persist_stored(config: Config, store: SessionStore, updates: dict[str, Any]) -> None:
    """Write page edits into the settings table; env-pinned fields are skipped."""
    clean = {key: value for key, value in updates.items() if key in STORED_KEYS}
    clean = {key: value for key, value in clean.items() if key not in config.env_overrides}
    store.set_settings({key: _encode(value) for key, value in clean.items()})


def migrate_files(config: Config, store: SessionStore) -> None:
    """One-time import of the old config.toml / <data_dir>/settings.toml.

    Runs only while the settings table holds none of our keys, so page edits
    are never overwritten by a stale file. Profiles from the old overlay land
    in the provider_profiles table.
    """
    values = store.settings()
    if any(key in values for key in STORED_KEYS):
        return

    seed: dict[str, Any] = {}
    toml_path = Path("config.toml")
    if toml_path.is_file():
        with toml_path.open("rb") as handle:
            raw = tomllib.load(handle)
        for section in ("provider", "agent"):
            for key, value in _as_dict(raw.get(section)).items():
                if key in STORED_KEYS:
                    seed[key] = value
        _import_profiles(_as_dict(raw.get("profiles")), store, seed)
    overlay_path = config.data_dir / "settings.toml"
    if overlay_path.is_file():
        with overlay_path.open("rb") as handle:
            page = tomllib.load(handle)
        for key, value in _as_dict(page.get("provider")).items():
            if key in STORED_KEYS:
                seed[key] = value
        _import_profiles(_as_dict(page.get("profiles")), store, seed)
    if seed:
        store.set_settings({key: _encode(value) for key, value in seed.items()})


def _import_profiles(section: dict[str, Any], store: SessionStore, seed: dict[str, Any]) -> None:
    """Move old TOML profiles into provider_profiles; 'active' becomes a marker."""
    for name, entry in section.items():
        if name == "active":
            if entry:
                seed["active_profile"] = str(entry)
            continue
        if not isinstance(entry, dict):
            continue
        row = cast("dict[str, Any]", entry)
        _ = store.upsert_profile(
            {
                "name": str(name),
                "base_url": row.get("base_url"),
                "model": row.get("model"),
                "api_key": row.get("api_key"),
                "temperature": row.get("temperature"),
                "stream_usage": bool(row.get("stream_usage")),
            }
        )


def _as_dict(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def describe(cfg: Config) -> str:
    """A short, key-free summary for the CLI banner."""
    parts = [
        f"model={cfg.model}",
        f"base={cfg.base_url}",
        f"permission={cfg.permission}",
        f"workspace={cfg.workspace}",
    ]
    missing = [] if cfg.api_key else ["api_key is not set"]
    return "  ".join(parts) + (f"  (! {', '.join(missing)})" if missing else "")
