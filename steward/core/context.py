"""Context budgeting.

Two rules matter more than the exact token counter:

1. Never split a turn. A turn is a user message plus every assistant/tool
   message after it. Cutting inside one produces tool results without the call
   that produced them, and every provider rejects that.
2. Shrink tool output before dropping old turns. A 40 KB `read_file` result is
   usually less valuable than the conversation that led to it.

The token estimate is deliberately crude (CJK ~= 1 token, other text ~= 4
chars/token). It only decides *when* to trim; exact accounting belongs to the
provider's usage report.
"""

from __future__ import annotations


def estimate_text(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) >= 0x2E80)
    return cjk + max(1, (len(text) - cjk) // 4)


def estimate_message(message: dict) -> int:
    total = estimate_text(str(message.get("content") or ""))
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        total += estimate_text(str(function.get("name") or ""))
        total += estimate_text(str(function.get("arguments") or ""))
    total += 8  # role / separators
    return total


def group_turns(messages: list[dict]) -> list[list[dict]]:
    """Split into turns, each starting at a user message."""
    turns: list[list[dict]] = []
    current: list[dict] | None = None
    for message in messages:
        if message.get("role") == "user":
            current = [message]
            turns.append(current)
        elif current is None:
            current = [message]
            turns.append(current)
        else:
            current.append(message)
    return turns


def _shrink_tool_message(message: dict, max_chars: int) -> dict:
    if message.get("role") != "tool":
        return message
    content = str(message.get("content") or "")
    if len(content) <= max_chars:
        return message
    head_len = max_chars * 2 // 3
    tail_len = max_chars - head_len
    head = content[:head_len]
    tail = content[-tail_len:] if tail_len else ""
    omitted = len(content) - len(head) - len(tail)
    shrunk = dict(message)
    shrunk["content"] = f"{head}\n...[truncated {omitted} chars]...\n{tail}"
    return shrunk


def fit(
    messages: list[dict],
    budget: int,
    max_tool_chars: int = 3_500,
) -> list[dict]:
    """Keep the newest whole turns that fit, plus every system message."""
    system = [message for message in messages if message.get("role") == "system"]
    body = [message for message in messages if message.get("role") != "system"]

    used = sum(estimate_message(message) for message in system)
    kept: list[list[dict]] = []
    for turn in reversed(group_turns(body)):
        cost = sum(estimate_message(message) for message in turn)
        if kept and used + cost > budget:
            break
        kept.insert(0, turn)
        used += cost

    flattened = [message for turn in kept for message in turn]
    return system + [_shrink_tool_message(m, max_tool_chars) for m in flattened]
