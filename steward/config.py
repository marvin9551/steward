"""Configuration: one TOML file plus a page-managed overlay plus environment.

Precedence, weakest to strongest:

1. defaults
2. `config.toml` — the file you edit by hand
3. `<data_dir>/settings.toml` — written by the web UI; the page edits this
   overlay instead of rewriting your commented file. It also stores named
   provider profiles (`[profiles.<name>]`) the page can switch between;
   `config.load()` ignores those sections.
4. `STEWARD_*` environment variables — a key can stay out of every file

No pydantic here on purpose. The config is a flat set of values; a dataclass
with explicit defaults is enough, and reading TOML with tomllib (stdlib) keeps
the dependency list at exactly one package.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PERMISSION_MODES = ("read-only", "ask-before-write", "workspace-write", "full")
DEFAULT_CONFIG_FILE = Path("config.toml")
OVERLAY_FILE = "settings.toml"
PROFILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,31}")
OVERLAY_HEADER = (
    "# Managed by the steward web UI; safe to delete.\n"
    "# Applied on top of config.toml. STEWARD_* environment variables still win.\n"
)


def _expand(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser()


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


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
    # these because a file would never win over them.
    env_overrides: list[str] = field(default_factory=list)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "steward.db"

    def prepare(self) -> "Config":
        """Create the data/workspace directories and validate the mode."""
        # "ask-before-write" is the deprecated spelling of workspace-write:
        # the old mode had the same decision table, so old settings keep
        # loading under the merged name.
        if self.permission == "ask-before-write":
            self.permission = "workspace-write"
        if self.permission not in PERMISSION_MODES:
            raise ValueError(
                f"permission must be one of {PERMISSION_MODES}, got {self.permission!r}"
            )
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self


def _coerce(name: str, value: object, current: object) -> object:
    """Convert a TOML/env value to the dataclass field's type."""
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, Path):
        return _expand(str(value))
    return str(value)


def load(path: str | Path | None = None) -> Config:
    """Assemble the config: defaults, config.toml, the UI overlay, then env.

    Environment wins so a key can stay out of the repository entirely:
        STEWARD_API_KEY, STEWARD_BASE_URL, STEWARD_MODEL,
        STEWARD_WORKSPACE, STEWARD_DATA_DIR, STEWARD_PERMISSION
    """
    cfg = Config()
    config_path = Path(path).expanduser() if path else DEFAULT_CONFIG_FILE

    raw: dict = {}
    if config_path.exists():
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)

    for section in ("provider", "agent"):
        for key, value in _as_dict(raw.get(section)).items():
            if key == "env_overrides" or not hasattr(cfg, key):
                continue
            setattr(cfg, key, _coerce(key, value, getattr(cfg, key)))

    env_map = {
        "STEWARD_API_KEY": "api_key",
        "STEWARD_BASE_URL": "base_url",
        "STEWARD_MODEL": "model",
        "STEWARD_WORKSPACE": "workspace",
        "STEWARD_DATA_DIR": "data_dir",
        "STEWARD_PERMISSION": "permission",
    }
    for env_name, attr in env_map.items():
        value = os.environ.get(env_name)
        if value:
            cfg.env_overrides.append(attr)
            setattr(cfg, attr, _coerce(attr, value, getattr(cfg, attr)))

    # The page's overlay lives in the data dir and loses to the environment,
    # so a secret exported as STEWARD_API_KEY can never be silently replaced.
    # The overlay must not move the directories it is looked up in.
    overlay = cfg.data_dir / OVERLAY_FILE
    if overlay.is_file():
        with overlay.open("rb") as handle:
            page = tomllib.load(handle)
        for section in ("provider", "agent"):
            for key, value in _as_dict(page.get(section)).items():
                if (
                    key in ("env_overrides", "data_dir", "workspace")
                    or key in cfg.env_overrides
                    or not hasattr(cfg, key)
                ):
                    continue
                setattr(cfg, key, _coerce(key, value, getattr(cfg, key)))

    return cfg.prepare()


def read_overlay(config: Config) -> dict:
    """The page-managed overlay, or {} if none exists yet."""
    path = config.data_dir / OVERLAY_FILE
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def write_overlay(config: Config, data: dict) -> Path:
    """Serialize the overlay. Two levels of nesting: `[profiles]` holds the
    active marker, `[profiles.<name>]` holds one profile each."""
    lines = [OVERLAY_HEADER.rstrip(), ""]
    for name, values in data.items():
        if not isinstance(values, dict):
            lines.append(f"{name} = {_toml_value(values)}")
            continue
        scalars = {k: v for k, v in values.items() if not isinstance(v, dict)}
        if scalars or not values:
            lines.append(f"[{name}]")
            for key, value in scalars.items():
                lines.append(f"{key} = {_toml_value(value)}")
            lines.append("")
        for key, nested in values.items():
            if isinstance(nested, dict):
                lines.append(f"[{name}.{key}]")
                for sub_key, value in nested.items():
                    lines.append(f"{sub_key} = {_toml_value(value)}")
                lines.append("")
    path = config.data_dir / OVERLAY_FILE
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def save_overlay(config: Config, section: str, updates: dict) -> Path:
    """Persist page edits to `<data_dir>/settings.toml`, merging what is there.

    Writing the overlay instead of `config.toml` is the point: your commented
    file stays yours, and deleting the overlay undoes every page change.
    """
    data = read_overlay(config)
    values = dict(_as_dict(data.get(section)))
    for key, value in updates.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value

    if values:
        data[section] = values
    else:
        data.pop(section, None)
    return write_overlay(config, data)


def save_profile(config: Config, name: str, values: dict) -> Path:
    """Upsert one provider profile under `[profiles.<name>]`."""
    profiles = dict(_as_dict(read_overlay(config).get("profiles")))
    profiles[name] = dict(_as_dict(profiles.get(name))) | dict(values)
    return write_overlay(config, {**read_overlay(config), "profiles": profiles})


def delete_profile(config: Config, name: str) -> bool:
    data = read_overlay(config)
    profiles = _as_dict(data.get("profiles"))
    if name not in profiles:
        return False
    del profiles[name]
    if profiles.get("active") == name:
        profiles.pop("active", None)
    data["profiles"] = profiles
    write_overlay(config, data)
    return True


def set_active_profile(config: Config, name: str) -> Path:
    """Record which profile the active provider came from."""
    data = read_overlay(config)
    profiles = _as_dict(data.get("profiles"))
    profiles["active"] = name
    data["profiles"] = profiles
    return write_overlay(config, data)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=False)


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

