"""Permission policy: what a tool call is allowed to do.

Three modes, from safest to loosest:

- ``read-only``        reads only; writes and shell are denied.
- ``workspace-write``  reads are free; every write, edit and shell command
                       asks the user first. The name records why the mode
                       exists: writes stay inside the workspace, so asking
                       is about review, not about reach.
- ``full``             no prompts (still not a sandbox).

``ask-before-write`` is accepted as a deprecated alias for
``workspace-write`` — the old spelling had the same decision table, so old
``config.toml`` / ``settings.toml`` files keep loading.

The decision is separate from execution so the same table can drive a CLI
prompt, a web approval card, or a test.
"""

from __future__ import annotations

READ_ONLY = "read-only"
WORKSPACE_WRITE = "workspace-write"
FULL = "full"

ASK_BEFORE_WRITE = "ask-before-write"
ALIASES: dict[str, str] = {ASK_BEFORE_WRITE: WORKSPACE_WRITE}

MODES = (READ_ONLY, WORKSPACE_WRITE, FULL)

WRITE_TOOLS = {"write_file", "edit_file"}
EXEC_TOOLS = {"run_shell"}

ALLOW = "allow"
ASK = "ask"
DENY = "deny"


def decide(mode: str, tool_name: str) -> str:
    mode = ALIASES.get(mode, mode)
    if mode == FULL:
        return ALLOW
    if mode == READ_ONLY:
        return DENY if tool_name in WRITE_TOOLS | EXEC_TOOLS else ALLOW
    # workspace-write: every mutation needs a human in the loop; reads are free.
    return ASK if tool_name in WRITE_TOOLS | EXEC_TOOLS else ALLOW
