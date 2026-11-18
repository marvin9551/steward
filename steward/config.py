"""Configuration: one TOML file plus environment overrides.

No pydantic here on purpose. The config is a flat set of values; a dataclass
with explicit defaults is enough, and reading TOML with tomllib (stdlib) keeps
the dependency list at exactly one package.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PERMISSION_MODES = ("read-only", "workspace-write", "full")
DEFAULT_CONFIG_FILE = Path("config.toml")


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

    @property
    def db_path(self) -> Path:
        return self.data_dir / "steward.db"

    def prepare(self) -> "Config":
        """Create the data/workspace directories and validate the mode."""
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
    """Load config.toml (if present), then apply STEWARD_* env overrides.

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
            if hasattr(cfg, key):
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
            setattr(cfg, attr, _coerce(attr, value, getattr(cfg, attr)))

    return cfg.prepare()


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

