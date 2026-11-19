"""File tools, all confined to the workspace.

Path policy lives here, not in the model: every path is resolved and checked
against ToolContext.workspace, so a `../../etc/passwd` in an argument is an
error rather than a leak. Read-only mode is enforced a layer up, in policy.
"""

from __future__ import annotations

from pathlib import Path

from steward.tools.base import ToolContext, tool

MAX_READ_LINES = 400


def _resolve(ctx: ToolContext, raw: str, *, write: bool) -> Path:
    if not raw:
        raise ValueError("path must be a non-empty string")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = ctx.workspace / candidate
    resolved = candidate.resolve()
    workspace = ctx.workspace.resolve()
    if not resolved.is_relative_to(workspace):
        action = "write" if write else "read"
        raise PermissionError(f"refusing to {action} outside the workspace: {raw}")
    return resolved


def _relative(ctx: ToolContext, path: Path) -> str:
    try:
        return str(path.relative_to(ctx.workspace.resolve()))
    except ValueError:
        return str(path)


@tool(
    "read_file",
    "Read a text file from the workspace. Returns line-numbered content. "
    "Use offset/limit to page through large files.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace."},
            "offset": {"type": "integer", "description": "1-based first line.", "default": 1},
            "limit": {"type": "integer", "description": "Max lines to return.", "default": 200},
        },
        "required": ["path"],
    },
)
def read_file(ctx: ToolContext, path: str, offset: int = 1, limit: int = 200) -> str:
    target = _resolve(ctx, path, write=False)
    if not target.is_file():
        return f"ERROR: no such file: {path}"
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    limit = max(1, min(int(limit), MAX_READ_LINES))
    start = max(0, int(offset) - 1)
    window = lines[start : start + limit]
    body = "\n".join(f"{start + i + 1:>6}\t{line}" for i, line in enumerate(window))
    remaining = len(lines) - start - len(window)
    if remaining > 0:
        body += f"\n... ({remaining} more lines; use offset={start + len(window) + 1})"
    return body or "(empty file)"


@tool(
    "write_file",
    "Create or overwrite a text file in the workspace. Creates parent directories.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace."},
            "content": {"type": "string", "description": "Full new file content."},
        },
        "required": ["path", "content"],
    },
)
def write_file(ctx: ToolContext, path: str, content: str) -> str:
    target = _resolve(ctx, path, write=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = str(content)
    target.write_text(data, encoding="utf-8")
    return f"wrote {len(data)} chars to {_relative(ctx, target)}"


@tool(
    "edit_file",
    "Replace an exact string in a file. The old string must occur exactly once "
    "unless replace_all is true; include enough context to make it unique.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace."},
            "old": {"type": "string", "description": "Exact text to replace."},
            "new": {"type": "string", "description": "Replacement text."},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["path", "old", "new"],
    },
)
def edit_file(
    ctx: ToolContext, path: str, old: str, new: str, replace_all: bool = False
) -> str:
    if not old:
        return "ERROR: old must not be empty; include the text to replace"
    target = _resolve(ctx, path, write=True)
    if not target.is_file():
        return f"ERROR: no such file: {path}"
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        return "ERROR: old string not found; read the file and copy the exact text"
    if count > 1 and not replace_all:
        return f"ERROR: old string matches {count} times; add context or set replace_all"
    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    target.write_text(updated, encoding="utf-8")
    return f"replaced {count if replace_all else 1} occurrence(s) in {_relative(ctx, target)}"


@tool(
    "list_dir",
    "List files and directories in the workspace. Directories end with '/'.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory relative to the workspace.", "default": "."},
        },
    },
)
def list_dir(ctx: ToolContext, path: str = ".") -> str:
    target = _resolve(ctx, path, write=False)
    if not target.is_dir():
        return f"ERROR: not a directory: {path}"
    entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    if not entries:
        return "(empty directory)"
    lines: list[str] = []
    for entry in entries[:500]:
        suffix = "/" if entry.is_dir() else ""
        size = "" if entry.is_dir() else f"  {entry.stat().st_size} B"
        lines.append(f"{entry.name}{suffix}{size}")
    if len(entries) > 500:
        lines.append(f"... ({len(entries) - 500} more entries)")
    return "\n".join(lines)
