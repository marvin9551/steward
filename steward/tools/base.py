"""Tool contract.

A tool is three things: a name, a JSON schema the model reads, and a function.
Registration is a decorator; execution is one function that enforces timeout,
argument errors and output truncation in a single place.

Why the schema is a dict and not a pydantic model: the model sees the JSON
Schema verbatim, so writing it explicitly keeps the contract visible and avoids
a library that hides which fields are required.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

MAX_TOOL_TIMEOUT = 300

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class ToolContext:
    """Everything a tool needs that the model must not control."""

    workspace: Path
    max_output: int = 20_000


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, parameters: dict[str, Any]) -> Callable[[F], F]:
    def decorator(fn: F) -> F:
        REGISTRY[name] = Tool(name=name, description=description, parameters=parameters, fn=fn)
        return fn

    return decorator


def schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": item.name,
                "description": item.description,
                "parameters": item.parameters,
            },
        }
        for item in REGISTRY.values()
    ]


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return (
        text[:head]
        + f"\n...[truncated {len(text) - limit} chars]...\n"
        + text[-tail:]
    )


async def execute(
    name: str,
    args: dict[str, Any],
    ctx: ToolContext,
    timeout: float = 120,
) -> str:
    """Run one tool call and always return a string for the model.

    Errors become tool results, not exceptions: the model can read "no such
    file" and adapt, while an exception would abort the whole turn.
    """
    item = REGISTRY.get(name)
    if item is None:
        return f"ERROR: unknown tool {name!r}. Available: {', '.join(REGISTRY)}"

    timeout = min(float(timeout), MAX_TOOL_TIMEOUT)
    try:
        if inspect.iscoroutinefunction(item.fn):
            coro = item.fn(ctx, **args)
        else:
            coro = asyncio.to_thread(item.fn, ctx, **args)
        output = await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return f"ERROR: {name} timed out after {timeout:g}s"
    except TypeError as exc:
        return f"ERROR: bad arguments for {name}: {exc}"
    except Exception as exc:  # noqa: BLE001 - the model gets the message
        return f"ERROR: {type(exc).__name__}: {exc}"

    return truncate(str(output), ctx.max_output)

