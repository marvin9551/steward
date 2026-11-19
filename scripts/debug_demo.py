"""Offline demo: drive the agent loop with a scripted provider.

No network, no API key. Use it to hit breakpoints in the runner, the tool
executor and the session store while stepping through a complete turn.

    python scripts/debug_demo.py

or pick "Steward: offline demo" in VS Code's Run and Debug panel.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# Allow running this file directly without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steward.config import Config  # noqa: E402
from steward.core.events import AgentEvent  # noqa: E402
from steward.core.runner import Runner  # noqa: E402
from steward.providers.base import ProviderResult, TextCallback  # noqa: E402
from steward.session.store import SessionStore  # noqa: E402


class ScriptedProvider:
    """First call asks for a tool, second call answers."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self,
        config: Config,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        on_text: TextCallback = None,
        on_reasoning: TextCallback = None,
    ) -> ProviderResult:
        self.calls += 1
        if self.calls == 1:
            arguments = json.dumps({"path": "demo.txt", "content": "hello from the debugger\n"})
            return ProviderResult(tool_calls=[{
                "id": "call_demo",
                "name": "write_file",
                "arguments": arguments,
            }])

        text = "Done. I wrote demo.txt."
        if on_text:
            on_text(text)
        return ProviderResult(
            text=text,
            usage={"prompt_tokens": 42, "completion_tokens": 8},
        )


def show(event: AgentEvent) -> None:
    print(f"[event] {event}")


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = Config(
            workspace=root / "workspace",
            data_dir=root / "data",
            model="scripted",
            permission="workspace-write",
            auto_approve=True,
        )
        config.workspace.mkdir(parents=True)
        config.data_dir.mkdir(parents=True)

        store = SessionStore(config.db_path)
        runner = Runner(
            config,
            store,
            on_event=show,
            provider=ScriptedProvider(),
        )

        reply = await runner.run_turn("debug-demo", "write demo.txt")

        print("\nfinal:", reply)
        roles: list[str] = [str(message["role"]) for message in store.messages("debug-demo")]
        print("messages:", roles)
        print("file:", (config.workspace / "demo.txt").read_text().strip())
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
