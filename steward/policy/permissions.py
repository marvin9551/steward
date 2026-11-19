"""Permission policy: what a tool call is allowed to do.

Four modes, from safest to loosest:

- ``read-only``          reads only; writes and shell are denied.
- ``ask-before-write``   reads allowed; every write, edit and shell command
                         asks the user first.
- ``workspace-write``    files are writable inside the workspace; shell asks.
- ``full``               no prompts (still not a sandbox).

The decision is separate from execution so the same table can drive a CLI
prompt, a web approval card, or a test.
"""

from __future__ import annotations

READ_ONLY = "read-only"
ASK_BEFORE_WRITE = "ask-before-write"
WORKSPACE_WRITE = "workspace-write"
FULL = "full"

MODES = (READ_ONLY, ASK_BEFORE_WRITE, WORKSPACE_WRITE, FULL)

WRITE_TOOLS = {"write_file", "edit_file"}
EXEC_TOOLS = {"run_shell"}

ALLOW = "allow"
ASK = "ask"
DENY = "deny"


def decide(mode: str, tool_name: str) -> str:
    if mode == FULL:
        return ALLOW
    if mode == READ_ONLY:
        return DENY if tool_name in WRITE_TOOLS | EXEC_TOOLS else ALLOW
    if mode == ASK_BEFORE_WRITE:
        # The whole point of this mode: nothing that changes the machine runs
        # without a human looking at it first.
        return ASK if tool_name in WRITE_TOOLS | EXEC_TOOLS else ALLOW
    # workspace-write: the filesystem tools are bounded by the tool context,
    # so only arbitrary command execution needs a human in the loop.
    return ASK if tool_name in EXEC_TOOLS else ALLOW
