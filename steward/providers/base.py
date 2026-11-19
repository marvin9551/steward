"""Provider contract.

A provider turns a list of OpenAI-style messages plus tool schemas into a
result. Streaming callbacks are optional so tests can call providers without
worrying about output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

TextCallback = Callable[[str], None] | None


@dataclass
class ProviderResult:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    """Upstream returned a non-2xx response or an unusable stream."""


class Provider(Protocol):
    async def __call__(
        self,
        config: Any,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        on_text: TextCallback = None,
        on_reasoning: TextCallback = None,
    ) -> ProviderResult: ...
