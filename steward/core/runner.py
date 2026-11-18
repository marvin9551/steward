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


class Runner:
    def __init__(
        self,
        config,
        store: SessionStore,
        on_event=None,
        approver=None,
        provider: Provider | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self._emit = on_event or (lambda event: None)
        self._approver = approver
        self._provider = provider or stream_chat
        self._tool_ctx = ToolContext(
            workspace=config.workspace,
            max_output=config.max_tool_output,
        )

    def emit(self, event: AgentEvent) -> None:
        self._emit(event)

    async def run_turn(self, session_id: str, user_text: str) -> str:
        self.store.append(session_id, "user", {"content": user_text})
        usage: dict = {}

        for step in range(1, self.config.max_steps + 1):
            messages = fit_messages(self.config, self.store, session_id)
            result = await self._provider(
                self.config,
                messages,
                schemas(),
                on_text=lambda text: self.emit(TextDelta(text)),
                on_reasoning=lambda text: self.emit(ReasoningDelta(text)),
            )
            usage = _merge_usage(usage, result.usage)

            tool_calls = [
                _to_openai_tool_call(call, index)
                for index, call in enumerate(result.tool_calls)
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

    async def _run_tool_call(self, session_id: str, call: dict) -> None:
        function = call["function"]
        name = function["name"]
        raw_args = function.get("arguments") or ""

        try:
            args = json.loads(raw_args) if raw_args.strip() else {}
        except ValueError:
            args = None
        if not isinstance(args, dict):
            args = {}
            output = f"ERROR: tool arguments were not valid JSON: {raw_args[:200]}"
        else:
            output = None

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

    async def _authorize_and_execute(self, name: str, args: dict) -> str:
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


def fit_messages(config, store: SessionStore, session_id: str) -> list[dict]:
    history = store.messages(session_id)
    messages = [{"role": "system", "content": build_system_prompt(config)}] + history
    return context.fit(
        messages,
        budget=config.context_budget,
        max_tool_chars=max(1_000, config.max_tool_output // 5),
    )


def _to_openai_tool_call(call: dict, index: int) -> dict:
    """Normalize a provider tool call into the shape stored and replayed."""
    return {
        "id": call.get("id") or f"call_{index}",
        "type": "function",
        "function": {
            "name": call.get("name") or "",
            "arguments": call.get("arguments") or "{}",
        },
    }


def _merge_usage(total: dict, usage: dict) -> dict:
    for key, value in (usage or {}).items():
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value
    return total
