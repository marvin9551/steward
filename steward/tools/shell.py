"""Shell tool.

Two deliberate limits:

- the child process gets a small environment allowlist instead of the parent's
  environment, so API keys do not leak into arbitrary commands;
- the command runs with cwd = workspace and has a hard timeout.

This is NOT a sandbox. A determined command can still escape the workspace;
use a container or an OS sandbox (bubblewrap / Seatbelt) if you need a real
boundary. See docs/decisions.md.
"""

from __future__ import annotations

import os
import subprocess

from steward.tools.base import ToolContext, tool

# Variables a normal CLI actually needs. Everything else (keys, tokens) is
# dropped on purpose.
_ENV_ALLOWLIST = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "TZ", "TMPDIR", "SYSTEMROOT", "COMSPEC", "PATHEXT",
)

MAX_SECONDS = 120


@tool(
    "run_shell",
    "Run a shell command in the workspace and return its combined output. "
    "Use non-interactive commands; long-running or destructive commands will "
    "need user approval first.",
    {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run."},
            "timeout": {
                "type": "integer",
                "description": "Seconds before the command is killed.",
                "default": 60,
            },
        },
        "required": ["command"],
    },
)
def run_shell(ctx: ToolContext, command: str, timeout: int = 60) -> str:
    if not command or not command.strip():
        return "ERROR: command is empty"
    timeout = max(1, min(int(timeout), MAX_SECONDS))
    env = {name: os.environ[name] for name in _ENV_ALLOWLIST if name in os.environ}

    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=ctx.workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout}s"

    parts: list[str] = []
    if completed.stdout:
        parts.append(completed.stdout.rstrip())
    if completed.stderr:
        parts.append("[stderr]\n" + completed.stderr.rstrip())
    if completed.returncode != 0:
        parts.append(f"[exit {completed.returncode}]")
    return "\n".join(parts) or "(no output)"
