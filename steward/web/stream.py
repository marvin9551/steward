"""A replayable event log behind the SSE endpoint.

A browser can disconnect at any moment (sleep, reload, network change). Rather
than treating that as the end of the transcript, every event gets a sequence
number, and a reconnecting client sends `Last-Event-ID`. The log then answers
with everything after it.

Two consequences worth knowing:

1. The log is a bounded deque of recent events, not a durable store. History
   lives in SQLite; this is only the tail of the *current* process.
2. A client whose cursor fell off the deque cannot be caught up from here, so
   `since()` reports `lost=True` and the page re-fetches the transcript instead
   of guessing. That is the whole point of the flag.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field

from steward.core.events import (
    Notice,
    ReasoningDelta,
    TextDelta,
    ToolCallEvent,
    ToolResultEvent,
    TurnDone,
)

DEFAULT_CAPACITY = 4_000


def encode(event: object) -> dict:
    """Turn one typed agent event into a JSON-ready payload.

    The `type` strings are the wire contract; the page switches on them.
    `TurnDone` carries the full reply as well as the deltas because a provider
    that does not stream (tests, some compatible APIs) would otherwise leave the
    page with an empty bubble.
    """
    if isinstance(event, TextDelta):
        return {"type": "text", "text": event.text}
    if isinstance(event, ReasoningDelta):
        return {"type": "reasoning", "text": event.text}
    if isinstance(event, ToolCallEvent):
        return {"type": "tool_call", "name": event.name, "args": event.args}
    if isinstance(event, ToolResultEvent):
        return {
            "type": "tool_result",
            "name": event.name,
            "output": event.output,
            "ok": event.ok,
        }
    if isinstance(event, Notice):
        return {"type": "notice", "text": event.text}
    if isinstance(event, TurnDone):
        return {
            "type": "turn_done",
            "text": event.text,
            "steps": event.steps,
            "usage": event.usage,
        }
    raise TypeError(f"not an agent event: {type(event).__name__}")


@dataclass(frozen=True)
class Batch:
    """What a subscriber misses between its cursor and now."""

    events: list[dict] = field(default_factory=list)
    lost: bool = False


class EventLog:
    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._items: deque[dict] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._seq = 0

    def publish(self, payload: dict) -> int:
        """Append one payload and return its sequence number."""
        with self._lock:
            self._seq += 1
            self._items.append({"seq": self._seq, **payload})
            return self._seq

    def since(self, cursor: int) -> Batch:
        with self._lock:
            if not self._items:
                return Batch()
            oldest = self._items[0]["seq"]
            lost = cursor > 0 and cursor + 1 < oldest
            return Batch([item for item in self._items if item["seq"] > cursor], lost)

    def tail(self, count: int = 100) -> list[dict]:
        with self._lock:
            return list(self._items)[-count:]

    @property
    def cursor(self) -> int:
        with self._lock:
            return self._seq
