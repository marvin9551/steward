"""Feishu channel: WebSocket long connection for events, raw HTTP for sends.

One Feishu self-built app drives the whole channel:

- the official ``lark-oapi`` SDK (optional extra ``[feishu]``) provides the
  WebSocket long connection: ``im.message.receive_v1`` events and card-action
  callbacks arrive with no public IP, no webhook and no signing logic;
- replies go out as plain OpenAPI HTTP calls with a cached
  ``tenant_access_token`` — short answers as text bubbles, anything richer
  (tables, code blocks, long text) as an interactive card with a markdown
  element, so tables render as tables;
- approvals become interactive cards with 允许一次 / 始终允许 / 拒绝 buttons;
  the button callback resolves the same ``ApprovalRegistry`` the web page
  uses, so IM sessions can run with the configured permission mode.

Sessions map 1:1 to conversations: ``feishu-<open_id>`` for DMs and
``feishu-g<chat_id>-<open_id>`` for groups, so each contact (and each member
inside a group) keeps an isolated history.

The module imports without lark-oapi installed — the pure helpers stay
testable, and ``start()`` reports the missing dependency instead of crashing.
The SDK ships no type stubs, so every touchpoint goes through ``getattr``
boundaries; the rest of the module is strictly typed.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import re
import threading
import time
from types import ModuleType
from typing import Any, cast

import httpx

from steward.web.service import WebService

FEISHU_DOMAIN = "https://open.feishu.cn"
TOKEN_REFRESH_MARGIN = 120.0
WS_RETRY_SECONDS = 5.0
MARKDOWN_HINTS = ("```", "\n|", "| ", "\n- ", "\n* ", "\n# ", "\n> ")

lark: ModuleType | None = (
    importlib.import_module("lark_oapi")
    if importlib.util.find_spec("lark_oapi")
    else None
)


def extract_text(message: dict[str, Any]) -> str:
    """Plain text of an inbound message; '' for types we do not handle yet."""
    content_type = str(message.get("message_type") or "")
    try:
        content = json.loads(str(message.get("content") or "{}"))
    except ValueError:
        return ""
    if not isinstance(content, dict):
        return ""
    body = cast(dict[str, Any], content)
    if content_type == "text":
        return str(body.get("text") or "").strip()
    if content_type == "post":
        # rich text: {"title": str, "content": [[{tag, text, href, ...}]]}
        parts: list[str] = []
        title = str(body.get("title") or "")
        if title:
            parts.append(title)
        for paragraph in cast(list[Any], body.get("content") or []):
            if not isinstance(paragraph, list):
                continue
            line = ""
            for element in cast(list[Any], paragraph):
                if isinstance(element, dict):
                    item = cast(dict[str, Any], element)
                    if item.get("tag") == "text":
                        line += str(item.get("text") or "")
            if line:
                parts.append(line)
        return "\n".join(parts).strip()
    return ""


def strip_mention_placeholder(text: str) -> str:
    """Group messages carry the @bot as ``@_user_1`` tokens; drop them."""
    cleaned = re.sub(r"@_user_\d+", "", text).strip()
    return cleaned or text.strip()


def session_id_for(chat_type: str, chat_id: str, open_id: str) -> str:
    """One history per contact; inside a group, one per member."""
    if chat_type == "group":
        return f"feishu-g{chat_id}-{open_id}"
    return f"feishu-{open_id}"


def wants_markdown_card(text: str) -> bool:
    """Rich content renders better as a card than as a plain text bubble."""
    if len(text) > 800:
        return True
    return any(hint in text for hint in MARKDOWN_HINTS)


def approval_card(name: str, args: dict[str, Any], approval_id: str) -> dict[str, Any]:
    """Interactive card whose buttons resolve the web-side approval registry."""
    return {
        "config": {"wide_screen_mode": True},
        "elements": [
            {
                "tag": "markdown",
                "content": f"**需要确认：`{name}`**\n这次调用会在你的工作区里执行。",
            },
            {
                "tag": "markdown",
                "content": f"```{json.dumps(args, ensure_ascii=False)[:600]}```",
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "允许一次"},
                        "type": "primary",
                        "value": {"approval_id": approval_id, "decision": "once"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "始终允许"},
                        "type": "default",
                        "value": {"approval_id": approval_id, "decision": "always"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "拒绝"},
                        "type": "danger",
                        "value": {"approval_id": approval_id, "decision": "deny"},
                    },
                ],
            },
        ],
    }


class FeishuChannel:
    """Owns the ws thread and all Feishu side effects for one steward instance."""

    def __init__(self, service: WebService) -> None:
        self.service = service
        self.running = False
        self.error = ""
        self._stop = threading.Event()
        self._token_expires_at = 0.0
        self._token = ""
        self._client: Any = None

    # -- lifecycle ------------------------------------------------------

    def start(self) -> bool:
        config = self.service.config
        if not (config.feishu_app_id and config.feishu_app_secret):
            self.error = "未配置 app_id / app_secret"
            return False
        if lark is None:
            self.error = "未安装 lark-oapi：pip install -e '.[feishu]'"
            self.service.store.append_log(
                "ERROR",
                "channel",
                "feishu.start",
                self.error,
                detail={"app_id": config.feishu_app_id},
            )
            return False

        def handle_message(data: Any) -> None:
            try:
                event = event_dict(data)
                self._schedule_inbound(event.get("message") or {}, event.get("sender") or {})
            except Exception as exc:  # noqa: BLE001 - the ws thread must survive
                self.error = f"message handler: {exc}"
                self.service.store.append_log(
                    "ERROR",
                    "channel",
                    "feishu.error",
                    f"飞书消息处理异常：{exc}",
                    detail={"error": str(exc)},
                )

        def handle_card_action(data: Any) -> Any:
            try:
                event = event_dict(data)
                self._resolve_card_action(event)
                return _card_action_response(
                    {"toast": {"type": "success", "content": "已记录"}}
                )
            except Exception as exc:  # noqa: BLE001
                self.error = f"card handler: {exc}"
                self.service.store.append_log(
                    "ERROR",
                    "channel",
                    "feishu.card_action",
                    f"飞书卡片回调处理异常：{exc}",
                    detail={"error": str(exc)},
                )
                return _card_action_response(
                    {"toast": {"type": "error", "content": "处理失败，请在网页里操作"}}
                )

        dispatcher = getattr(lark, "EventDispatcherHandler")  # noqa: B009 - untyped SDK seam
        handler = (
            dispatcher.builder("", "")
            .register_p2_im_message_receive_v1(handle_message)
            .register_p2_card_action_trigger(handle_card_action)
            .build()
        )

        def ws_loop() -> None:
            # lark_oapi.ws.client binds a module-level `loop = asyncio.get_event_loop()`
            # at import time. Because `steward.channel.feishu` is imported inside
            # FastAPI's running lifespan coroutine, that module-level `loop`
            # captures uvicorn's main event loop! Calling `ws_client.start()` on
            # this worker thread would then call `loop.run_until_complete()` on
            # the already-running uvicorn loop and raise:
            # "RuntimeError: This event loop is already running".
            # Giving this thread its own event loop and rebinding
            # `lark_oapi.ws.client.loop` before constructing `Client` (whose
            # `asyncio.Lock()` also binds to the current thread's loop) fixes it.
            ws_event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_event_loop)
            ws_client_mod = importlib.import_module("lark_oapi.ws.client")
            setattr(ws_client_mod, "loop", ws_event_loop)  # noqa: B010 - untyped SDK seam

            ws_client = getattr(lark, "ws").Client(  # noqa: B009 - untyped SDK seam
                config.feishu_app_id,
                config.feishu_app_secret,
                event_handler=handler,
            )

            # The SDK reconnects internally; a failed handshake raises out of
            # start(), so retry with a fixed delay. lark.ws has no stop() —
            # the daemon thread dies with the process.
            while not self._stop.is_set():
                try:
                    ws_client.start()
                    self._stop.wait(WS_RETRY_SECONDS)
                except Exception as exc:  # noqa: BLE001 - reconnect below
                    self.error = f"connection: {exc}"
                    self.service.store.append_log(
                        "ERROR",
                        "channel",
                        "feishu.error",
                        f"飞书长连接断开，{WS_RETRY_SECONDS}s 后重试：{exc}",
                        detail={"error": str(exc)},
                    )
                    if self._stop.wait(WS_RETRY_SECONDS):
                        return

        threading.Thread(target=ws_loop, daemon=True, name="steward-feishu-ws").start()
        self.running = True
        self.error = ""
        self.service.store.append_log(
            "INFO",
            "channel",
            "feishu.start",
            f"飞书长连接已启动（app_id={config.feishu_app_id}）",
            detail={"app_id": config.feishu_app_id},
        )
        return True

    # -- inbound --------------------------------------------------------

    def _schedule_inbound(self, message: dict[str, Any], sender: dict[str, Any]) -> None:
        chat_type = str(message.get("chat_type") or "")
        if chat_type == "group" and not message.get("mentions"):
            return  # group messages only matter when the bot is mentioned
        text = strip_mention_placeholder(extract_text(message))
        chat_id = str(message.get("chat_id") or "")
        if not text:
            self._send_text(chat_id, "暂时只支持文本消息")
            return
        raw_ids = sender.get("sender_id")
        ids = cast(dict[str, Any], raw_ids) if isinstance(raw_ids, dict) else {}
        sender_id = str(ids.get("open_id") or "")
        session_id = session_id_for(chat_type, chat_id, sender_id)
        self.service.store.append_log(
            "INFO",
            "channel",
            "feishu.inbound",
            f"收到飞书消息：{' '.join(text.split())[:100]}",
            session_id=session_id,
            detail={
                "chat_id": chat_id,
                "chat_type": chat_type,
                "sender_open_id": sender_id,
                "text_preview": text[:200],
            },
        )
        coroutine = self._run_turn(session_id, chat_id, text)
        asyncio.run_coroutine_threadsafe(coroutine, self.service.loop)

    async def _run_turn(self, session_id: str, chat_id: str, text: str) -> None:
        service = self.service
        service.register_turn_callbacks(
            session_id,
            observer=lambda payload: self._on_event(chat_id, payload),
            approver=lambda name, args: self._approve(chat_id, name, args),
        )
        try:
            await service.start_turn(session_id, text)
        except Exception as exc:  # noqa: BLE001 - the IM user should see it
            self._send_text(chat_id, f"无法开始处理：{exc}"[:300])

    def _on_event(self, chat_id: str, payload: dict[str, Any]) -> None:
        kind = payload.get("type")
        if kind == "turn_done":
            text = str(payload.get("text") or "")
            if text:
                self._send_reply(chat_id, text)
        elif kind == "error":
            self._send_text(chat_id, f"出错了：{payload.get('text', '')}"[:800])

    async def _approve(self, chat_id: str, name: str, args: dict[str, Any]) -> bool:
        from steward.web.service import APPROVAL_TIMEOUT, DECISION_ALWAYS, DECISION_ONCE

        if self.service.is_always_allowed(name):
            return True
        approval = self.service.create_approval(name, args)
        self._send_card(chat_id, approval_card(name, dict(args), approval.id))
        decision = await self.service.approvals.wait(approval.id, APPROVAL_TIMEOUT)
        resolved = decision or "deny"
        self.service.publish(
            {
                "type": "approval_resolved",
                "id": approval.id,
                "decision": resolved,
            }
        )
        allowed = decision in (DECISION_ONCE, DECISION_ALWAYS)
        self.service.store.append_log(
            "INFO" if allowed else "WARN",
            "tool",
            "tool.approval",
            f"飞书审批工具 {name}：{resolved}",
            detail={
                "approval_id": approval.id,
                "chat_id": chat_id,
                "name": name,
                "decision": resolved,
                "allowed": allowed,
                "args": args,
            },
        )
        if decision == DECISION_ALWAYS:
            self.service.remember_always_allowed(name)
        return allowed

    def _resolve_card_action(self, event: dict[str, Any]) -> None:
        """A button click arrives on the ws thread; resolve on the loop."""
        action = cast(dict[str, Any], event.get("action") or {})
        value = cast(dict[str, Any], action.get("value") or {})
        approval_id = str(value.get("approval_id") or "")
        decision = str(value.get("decision") or "")
        if approval_id and decision:
            self.service.store.append_log(
                "INFO",
                "channel",
                "feishu.card_action",
                f"收到飞书审批卡片点击（id={approval_id}, decision={decision}）",
                detail={"approval_id": approval_id, "decision": decision},
            )

            async def resolve() -> bool:
                return self.service.resolve_approval(approval_id, decision)

            asyncio.run_coroutine_threadsafe(resolve(), self.service.loop)

    # -- outbound -------------------------------------------------------

    def _send_reply(self, chat_id: str, text: str) -> None:
        if wants_markdown_card(text):
            self._send_card(
                chat_id,
                {
                    "schema": "2.0",
                    "body": {"elements": [{"tag": "markdown", "content": text}]},
                },
            )
        else:
            self._send_text(chat_id, text)

    def _send_text(self, chat_id: str, text: str) -> None:
        self._send_raw(chat_id, "text", json.dumps({"text": text}, ensure_ascii=False))

    def _send_card(self, chat_id: str, card: dict[str, Any]) -> None:
        self._send_raw(chat_id, "interactive", json.dumps(card, ensure_ascii=False))

    def _send_raw(self, chat_id: str, msg_type: str, content: str) -> None:
        response = httpx.post(
            f"{FEISHU_DOMAIN}/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={
                "Authorization": f"Bearer {self._tenant_token()}",
                "Content-Type": "application/json",
            },
            json={"receive_id": chat_id, "msg_type": msg_type, "content": content},
            timeout=15.0,
        )
        if not response.headers.get("content-type", "").startswith("application/json"):
            self.error = f"send failed: HTTP {response.status_code}"
            self.service.store.append_log(
                "ERROR",
                "channel",
                "feishu.error",
                f"飞书消息发送失败：HTTP {response.status_code}",
                detail={"chat_id": chat_id, "msg_type": msg_type, "status_code": response.status_code},
            )
            return
        payload = cast(dict[str, Any], response.json())
        if response.status_code >= 400 or payload.get("code") not in (0, None):
            self.error = f"send failed: HTTP {response.status_code} {payload.get('msg', '')}"
            self.service.store.append_log(
                "ERROR",
                "channel",
                "feishu.error",
                f"飞书消息发送失败：HTTP {response.status_code} {payload.get('msg', '')}",
                detail={
                    "chat_id": chat_id,
                    "msg_type": msg_type,
                    "status_code": response.status_code,
                    "code": payload.get("code"),
                    "msg": payload.get("msg"),
                },
            )
            return
        self.service.store.append_log(
            "INFO",
            "channel",
            "feishu.outbound",
            f"飞书消息已发送（type={msg_type}）",
            detail={"chat_id": chat_id, "msg_type": msg_type},
        )

    def _tenant_token(self) -> str:
        """App credential token, cached until shortly before it expires."""
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token
        response = httpx.post(
            f"{FEISHU_DOMAIN}/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self.service.config.feishu_app_id,
                "app_secret": self.service.config.feishu_app_secret,
            },
            timeout=15.0,
        )
        payload = response.json()
        token = str(payload.get("tenant_access_token") or "")
        if not token:
            self.error = f"token failed: {payload.get('msg', '')}"
            self.service.store.append_log(
                "ERROR",
                "channel",
                "feishu.error",
                f"获取飞书 tenant_access_token 失败：{payload.get('msg', '')}",
                detail={"code": payload.get("code"), "msg": payload.get("msg")},
            )
            return self._token
        self._token = token
        self._token_expires_at = now + float(payload.get("expire", 7200)) - TOKEN_REFRESH_MARGIN
        return self._token


def event_dict(data: Any) -> dict[str, Any]:
    """lark hands typed P2 envelope models (`{"schema": "2.0", "header": ..., "event": {...}}`);
    marshal to a plain dict and unwrap the inner `event` body for uniform access."""
    if lark is None:
        return {}
    raw: Any = json.loads(getattr(lark, "JSON").marshal(data))  # noqa: B009 - untyped SDK seam
    if not isinstance(raw, dict):
        return {}
    envelope = cast(dict[str, Any], raw)
    inner = envelope.get("event")
    if isinstance(inner, dict):
        return cast(dict[str, Any], inner)
    return envelope


def _card_action_response(payload: dict[str, Any]) -> Any:
    module = importlib.import_module(
        "lark_oapi.event.callback.model.p2_card_action_trigger"
    )
    return getattr(module, "P2CardActionTriggerResponse")(payload)  # noqa: B009 - untyped SDK seam
