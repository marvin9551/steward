"""The agent loop.

This is the whole harness: call the model, run any tool calls it asks for, feed
the results back, stop when it answers or the step budget runs out. Everything
else in the package exists to make one of those four steps observable,
persistent or safe.

It is deliberately unaware of the terminal. Events go out through `on_event`,
which is how the same loop will drive a web UI later.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

from steward.config import Config
from steward.core import context
from steward.core.events import (
    AgentEvent,
    Notice,
    ReasoningDelta,
    TextDelta,
    ToolCallEvent,
    ToolResultEvent,
    TurnDone,
)
from steward.core.prompt import build_system_prompt
from steward.policy.permissions import ASK, DENY, decide
from steward.providers.base import Provider
from steward.providers.openai_compat import stream_chat
from steward.session.store import SessionStore
from steward.tools import execute, schemas
from steward.tools.base import ToolContext

EventSink: TypeAlias = Callable[[AgentEvent], None]
Approver: TypeAlias = Callable[[str, dict[str, Any]], Awaitable[bool]]


def _ignore_event(_event: AgentEvent) -> None:
    """Default event sink so the loop also runs without a UI attached."""


class Runner:
    def __init__(
        self,
        config: Config,
        store: SessionStore,
        on_event: EventSink | None = None,
        approver: Approver | None = None,
        provider: Provider | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self._emit: EventSink = on_event or _ignore_event
        self._approver: Approver | None = approver
        self._provider: Provider = provider or stream_chat
        self._tool_ctx = ToolContext(
            workspace=config.workspace,
            max_output=config.max_tool_output,
        )

    def emit(self, event: AgentEvent) -> None:
        self._emit(event)

    async def run_turn(self, session_id: str, user_text: str) -> str:
        self.store.append(session_id, "user", {"content": user_text})
        usage: dict[str, int] = {}

        for step in range(1, self.config.max_steps + 1):
            messages = fit_messages(self.config, self.store, session_id)
            tool_schemas: list[dict[str, Any]] = schemas()
            result = await self._provider(
                self.config,
                messages,
                tool_schemas,
                on_text=lambda text: self.emit(TextDelta(text)),
                on_reasoning=lambda text: self.emit(ReasoningDelta(text)),
            )
            provider_usage: dict[str, Any] = result.usage
            usage = _merge_usage(usage, provider_usage)

            provider_calls: list[dict[str, Any]] = result.tool_calls
            tool_calls = [
                _to_openai_tool_call(call, index)
                for index, call in enumerate(provider_calls)
            ]
            self.store.append(
                session_id,
                "assistant",
                {"content": result.text, "tool_calls": tool_calls or None},
            )

            if not tool_calls:
                self.emit(TurnDone(text=result.text, steps=step, usage=usage))
                return result.text

            for call in tool_calls:
                await self._run_tool_call(session_id, call)

        self.emit(Notice(f"stopped: reached max_steps={self.config.max_steps}"))
        return "(stopped: step budget exhausted)"

    async def _run_tool_call(self, session_id: str, call: dict[str, Any]) -> None:
        function = call["function"]
        name: str = function["name"]
        raw_args: str = function.get("arguments") or ""

        try:
            parsed: dict[str, Any] | None = json.loads(raw_args) if raw_args.strip() else {}
        except ValueError:
            parsed = None

        args: dict[str, Any] = {}
        output: str | None
        if isinstance(parsed, dict):
            args = parsed
            output = None
        else:
            output = f"ERROR: tool arguments were not valid JSON: {raw_args[:200]}"

        self.emit(ToolCallEvent(name=name, args=args))

        if output is None:
            output = await self._authorize_and_execute(name, args)

        self.store.append(
            session_id,
            "tool",
            {"tool_call_id": call["id"], "name": name, "content": output},
        )
        self.emit(
            ToolResultEvent(
                name=name,
                output=output,
                ok=not output.startswith("ERROR:"),
            )
        )

    async def _authorize_and_execute(self, name: str, args: dict[str, Any]) -> str:
        decision = decide(self.config.permission, name)

        if decision == DENY:
            return (
                f"ERROR: tool {name} is denied in permission mode "
                f"{self.config.permission!r}"
            )

        if decision == ASK and not self.config.auto_approve:
            if self._approver is None:
                return f"ERROR: approval required for {name} but no approver is attached"
            if not await self._approver(name, args):
                return f"ERROR: the user denied this {name} call"

        return await execute(name, args, self._tool_ctx)


def fit_messages(
    config: Config,
    store: SessionStore,
    session_id: str,
) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = store.messages(session_id)
    system: dict[str, Any] = {"role": "system", "content": build_system_prompt(config)}
    return context.fit(
        [system, *history],
        budget=config.context_budget,
        max_tool_chars=max(1_000, config.max_tool_output // 5),
    )


def _to_openai_tool_call(call: dict[str, Any], index: int) -> dict[str, Any]:
    """Normalize a provider tool call into the shape stored and replayed."""
    return {
        "id": call.get("id") or f"call_{index}",
        "type": "function",
        "function": {
            "name": call.get("name") or "",
            "arguments": call.get("arguments") or "{}",
        },
    }


def _merge_usage(total: dict[str, int], usage: dict[str, Any]) -> dict[str, int]:
    for key, value in usage.items():
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value
    return total
