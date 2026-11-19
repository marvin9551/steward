"""Typed events emitted while a turn runs.

The CLI prints them; a future web UI would serialize them. Keeping them typed
(rather than passing raw dicts around) is what makes a second frontend cheap:
the runner never knows how output is rendered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeAlias


@dataclass
class TextDelta:
    text: str


@dataclass
class ReasoningDelta:
    text: str


@dataclass
class ToolCallEvent:
    name: str
    args: dict[str, Any]


@dataclass
class ToolResultEvent:
    name: str
    output: str
    ok: bool


@dataclass
class Notice:
    text: str


@dataclass
class TurnDone:
    text: str
    steps: int
    usage: dict[str, Any] = field(default_factory=dict)


AgentEvent: TypeAlias = (
    TextDelta | ReasoningDelta | ToolCallEvent | ToolResultEvent | Notice | TurnDone
)
