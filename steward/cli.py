"""Command line front end.

Everything here is presentation: parse flags, print events, ask for approval.
The agent itself never imports this module.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from steward import __version__
from steward.config import PERMISSION_MODES, Config, apply_stored, describe, load, migrate_files
from steward.core.events import (
    AgentEvent,
    Notice,
    ReasoningDelta,
    TextDelta,
    ToolCallEvent,
    ToolResultEvent,
)
from steward.core.runner import Runner
from steward.providers.base import ProviderError
from steward.session.store import SessionStore

DIM = "\033[2m"
RESET = "\033[0m"


class Renderer:
    """Print agent events. Tracks line state so streamed text and tool lines mix."""

    def __init__(self, show_reasoning: bool) -> None:
        self.show_reasoning = show_reasoning
        self._at_line_start = True

    def newline(self) -> None:
        if not self._at_line_start:
            sys.stdout.write("\n")
            self._at_line_start = True

    def __call__(self, event: AgentEvent) -> None:
        if isinstance(event, TextDelta):
            sys.stdout.write(event.text)
            sys.stdout.flush()
            self._at_line_start = event.text.endswith("\n")
        elif isinstance(event, ReasoningDelta):
            if self.show_reasoning:
                sys.stdout.write(f"{DIM}{event.text}{RESET}")
                sys.stdout.flush()
                self._at_line_start = False
        elif isinstance(event, ToolCallEvent):
            self.newline()
            print(f"  -> {event.name}({_compact(event.args)})")
        elif isinstance(event, ToolResultEvent):
            self.newline()
            status = "ok" if event.ok else "error"
            first = event.output.strip().splitlines()[0] if event.output.strip() else ""
            print(f"     {status}: {first[:180]}")
        elif isinstance(event, Notice):
            self.newline()
            print(f"[{event.text}]")
        else:
            # The AgentEvent union is exhaustive; what remains is TurnDone.
            self.newline()


def _compact(value: dict[str, Any], limit: int = 220) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="steward",
        description="A small, auditable personal agent.",
    )
    parser.add_argument("--version", action="version", version=f"steward {__version__}")
    sub = parser.add_subparsers(dest="command")

    chat = sub.add_parser("chat", help="start a conversation (default command)")
    chat.add_argument("--session", help="resume or create this session id")
    chat.add_argument("--new", action="store_true", help="always start a new session")
    chat.add_argument("--model", help="override the configured model")
    chat.add_argument("--workspace", help="override the workspace directory")
    chat.add_argument("--permission", choices=PERMISSION_MODES, help="override the permission mode")
    chat.add_argument("--yes", action="store_true", help="auto-approve shell commands")
    chat.add_argument("--reasoning", action="store_true", help="print streamed reasoning")

    sub.add_parser("sessions", help="list stored sessions")

    web = sub.add_parser("web", help="serve the local web UI")
    web.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    web.add_argument("--port", type=int, default=8765, help="bind port (default: 8765)")
    web.add_argument("--no-open", action="store_true", help="do not open a browser")
    web.add_argument("--quiet", action="store_true", help="do not log requests")

    history = sub.add_parser("history", help="print one session's messages")
    history.add_argument("session")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = load()
    except Exception as exc:  # noqa: BLE001 - config problems are user-facing
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    if args.command == "sessions":
        return _cmd_sessions(config)
    if args.command == "history":
        return _cmd_history(config, args.session)
    if args.command == "web":
        return _cmd_web(config, args)

    model = getattr(args, "model", None)
    if model:
        config.model = model
    workspace = getattr(args, "workspace", None)
    if workspace:
        config.workspace = load_workspace(workspace)
    permission = getattr(args, "permission", None)
    if permission:
        config.permission = permission
    if getattr(args, "yes", False):
        config.auto_approve = True
    if getattr(args, "reasoning", False):
        config.show_reasoning = True

    try:
        return asyncio.run(_chat(config, args))
    except KeyboardInterrupt:
        print("\nbye")
        return 0


def load_workspace(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _chat(config: Config, args: argparse.Namespace) -> int:
    store = SessionStore(config.db_path)
    if getattr(args, "new", False):
        session_id = SessionStore.new_session_id()
    else:
        session_id = getattr(args, "session", None) or store.latest_session()
        session_id = session_id or SessionStore.new_session_id()

    renderer = Renderer(config.show_reasoning)
    runner = Runner(
        config,
        store,
        on_event=renderer,
        approver=_approve if not config.auto_approve else None,
    )

    print(f"steward {__version__}")
    print(f"  {describe(config)}")
    print(f"  session {session_id}    (/new, /sessions, /exit)\n")

    while True:
        try:
            line = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        if line == "/new":
            session_id = SessionStore.new_session_id()
            print(f"  new session {session_id}")
            continue
        if line == "/sessions":
            _print_sessions(store)
            continue
        if line.startswith("/"):
            print("  unknown command; try /new, /sessions, /exit")
            continue

        print("steward › ", end="", flush=True)
        try:
            await runner.run_turn(session_id, line)
        except ProviderError as exc:
            renderer.newline()
            print(f"[provider error] {exc}")
        except KeyboardInterrupt:
            renderer.newline()
            print("[cancelled]")

    store.close()
    return 0


async def _approve(name: str, args: dict[str, Any]) -> bool:
    summary = _compact(args, 240)
    answer = await asyncio.to_thread(
        input, f"\n  approve {name} {summary}? [y/N] "
    )
    return answer.strip().lower() in ("y", "yes")


def _cmd_sessions(config: Config) -> int:
    store = SessionStore(config.db_path)
    _print_sessions(store)
    store.close()
    return 0


def _cmd_web(config: Config, args: argparse.Namespace) -> int:
    from steward.web.auth import ensure_default_user
    from steward.web.server import WebUnavailable, find_port, serve, url_for

    try:
        port = find_port(args.host, args.port)
    except OSError as exc:
        print(f"cannot start the web server: {exc}", file=sys.stderr)
        return 1

    # One-time import of the old config.toml / settings.toml, if any. The
    # service itself never reads files, so tests stay hermetic.
    store = SessionStore(config.db_path)
    migrate_files(config, store)
    _ = apply_stored(config, store)
    created = ensure_default_user(store)
    store.close()

    print(f"steward {__version__}")
    print(f"  {describe(config)}")

    if created is not None:
        username, password = created
        print(f"  account {username} / {password}")
        print("    ↑ 首次启动生成的默认账号，登录后请在「设置」中修改密码")

    print(f"  web {url_for(args.host, port)}    (ctrl-c to stop)\n")

    try:
        _ = serve(config, args.host, port, quiet=args.quiet, open_browser=not args.no_open)
    except WebUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nbye")
    return 0


def _print_sessions(store: SessionStore) -> None:
    sessions = store.sessions()
    if not sessions:
        print("  (no saved sessions)")
        return
    print(f"  {'session':<24} {'msgs':>5}  {'updated (UTC)':<19}  preview")
    for item in sessions:
        preview = _payload_preview(item.get("last_payload"))
        print(
            f"  {item['session_id']:<24} {item['messages']:>5}  "
            f"{item['updated_at']:<19}  {preview}"
        )


def _payload_preview(raw: str | None, limit: int = 60) -> str:
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
        text = str(payload.get("content") or "")
    except ValueError:
        text = raw
    text = " ".join(text.split())
    return text[:limit]


def _cmd_history(config: Config, session_id: str) -> int:
    store = SessionStore(config.db_path)
    if not store.exists(session_id):
        print(f"  no such session: {session_id}", file=sys.stderr)
        store.close()
        return 1
    for message in store.messages(session_id):
        role = message["role"]
        content = str(message.get("content") or "").strip()
        print(f"\n[{role}] {content[:500]}")
        tool_calls: list[dict[str, Any]] = message.get("tool_calls") or []
        for call in tool_calls:
            function: dict[str, Any] = call.get("function") or {}
            print(f"  -> {function.get('name')}({function.get('arguments')})")
    store.close()
    return 0
