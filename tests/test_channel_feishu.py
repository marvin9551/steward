"""Feishu channel: message extraction, session mapping, reply format, approvals."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from steward.channel.feishu import (
    approval_card,
    event_dict,
    extract_text,
    session_id_for,
    strip_mention_placeholder,
    wants_markdown_card,
)


def text_message(text: str, chat_type: str = "p2p") -> dict[str, Any]:
    return {
        "message_type": "text",
        "chat_type": chat_type,
        "chat_id": "oc_chat",
        "content": json.dumps({"text": text}),
    }


class TestExtractText(unittest.TestCase):
    def test_text_message(self) -> None:
        self.assertEqual(extract_text(text_message("你好")), "你好")

    def test_post_message_collects_text_paragraphs(self) -> None:
        message = {
            "message_type": "post",
            "content": json.dumps(
                {
                    "title": "标题",
                    "content": [
                        [{"tag": "text", "text": "第一段"}],
                        [{"tag": "text", "text": "第二段"}],
                    ],
                }
            ),
        }
        self.assertEqual(extract_text(message), "标题\n第一段\n第二段")

    def test_unknown_type_is_empty(self) -> None:
        self.assertEqual(extract_text({"message_type": "image", "content": "{}"}), "")
        self.assertEqual(extract_text({"message_type": "text", "content": "not-json"}), "")


class TestStripMention(unittest.TestCase):
    def test_drops_the_bot_placeholder(self) -> None:
        self.assertEqual(strip_mention_placeholder("@_user_1 帮我看看"), "帮我看看")

    def test_keeps_text_without_mentions(self) -> None:
        self.assertEqual(strip_mention_placeholder("直接问"), "直接问")


class TestSessionMapping(unittest.TestCase):
    def test_dm_is_one_session_per_contact(self) -> None:
        self.assertEqual(
            session_id_for("p2p", "oc_chat", "ou_abc"), "feishu-ou_abc"
        )

    def test_group_isolates_members(self) -> None:
        self.assertEqual(
            session_id_for("group", "oc_group", "ou_abc"), "feishu-goc_group-ou_abc"
        )
        self.assertNotEqual(
            session_id_for("group", "oc_group", "ou_abc"),
            session_id_for("group", "oc_group", "ou_other"),
        )


class TestReplyFormat(unittest.TestCase):
    def test_short_plain_text_stays_a_text_bubble(self) -> None:
        self.assertFalse(wants_markdown_card("就一句话"))

    def test_tables_code_and_long_text_become_cards(self) -> None:
        self.assertTrue(wants_markdown_card("对照：\n| a | b |\n| - | - |"))
        self.assertTrue(wants_markdown_card("看代码：\n```print(1)```"))
        self.assertTrue(wants_markdown_card("x" * 900))


class TestApprovalCard(unittest.TestCase):
    def test_buttons_carry_the_approval_id(self) -> None:
        card = approval_card("run_shell", {"command": "ls"}, "ap_1")
        buttons = card["elements"][2]["actions"]
        self.assertEqual([b["text"]["content"] for b in buttons], ["允许一次", "始终允许", "拒绝"])
        for button, decision in zip(buttons, ("once", "always", "deny"), strict=True):
            self.assertEqual(button["value"], {"approval_id": "ap_1", "decision": decision})


class TestServiceWiring(unittest.TestCase):
    """The service-side hooks exist even without lark-oapi installed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_feishu_status_reports_unconfigured(self) -> None:
        from steward.config import Config
        from steward.web.service import WebService

        async def scenario() -> dict[str, Any]:
            config = Config(
                workspace=Path(self._tmp.name) / "ws",
                data_dir=Path(self._tmp.name) / "data",
                model="fake",
                permission="workspace-write",
            ).prepare()
            service = WebService(config)
            try:
                return service.feishu_status()
            finally:
                await service.close()

        status = asyncio.run(scenario())
        self.assertFalse(status["configured"])
        self.assertFalse(status["running"])
        self.assertEqual(status["error"], "")

    def test_event_dict_unwraps_p2_event_envelope(self) -> None:
        from steward.channel import feishu as feishu_mod

        if feishu_mod.lark is None:
            self.skipTest("lark_oapi not installed")

        from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import (  # type: ignore[import-untyped]
            P2ImMessageReceiveV1,
        )

        raw = json.dumps(
            {
                "schema": "2.0",
                "header": {"event_type": "im.message.receive_v1"},
                "event": {
                    "message": {
                        "chat_id": "oc_123",
                        "chat_type": "p2p",
                        "message_type": "text",
                        "content": json.dumps({"text": "你好"}),
                    },
                    "sender": {"sender_id": {"open_id": "ou_abc"}},
                },
            }
        )
        unmarshaled = getattr(feishu_mod.lark, "JSON").unmarshal(raw, P2ImMessageReceiveV1)  # noqa: B009
        extracted = event_dict(unmarshaled)
        self.assertEqual(extracted["message"]["chat_id"], "oc_123")
        self.assertEqual(extract_text(extracted["message"]), "你好")


if __name__ == "__main__":
    unittest.main()
