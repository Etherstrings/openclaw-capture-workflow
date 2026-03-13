import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.models import IngestRequest, SummaryResult
from openclaw_capture_workflow.telegram import (
    _brief_value_line,
    _extract_priority_project_lines,
    _jarvis_brief_line,
    _jarvis_intro_line,
    _jarvis_judgment_line,
    _one_line_summary,
    _what_is_it_line,
    _why_it_matters_line,
    _worth_it_line,
)


class TelegramFormatTest(unittest.TestCase):
    def test_one_line_summary_picks_first_sentence(self) -> None:
        text = "这是第一句。这里是第二句。"
        self.assertEqual(_one_line_summary(text), "这是第一句")

    def test_extract_priority_project_lines_prefers_project_and_github(self) -> None:
        summary = SummaryResult(
            title="t",
            primary_topic="p",
            secondary_topics=[],
            entities=[],
            conclusion="c",
            bullets=[
                "技能名: 美股财报深度分析 Skill",
                "项目仓库: star23/Day1Global-Skills",
                "仓库地址: https://github.com/star23/Day1Global-Skills",
            ],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        lines = _extract_priority_project_lines(summary)
        self.assertEqual(lines[0], "项目: star23/Day1Global-Skills")
        self.assertEqual(lines[1], "GitHub: https://github.com/star23/Day1Global-Skills")

    def test_brief_value_line_for_video_partial(self) -> None:
        summary = SummaryResult(
            title="t",
            primary_topic="p",
            secondary_topics=[],
            entities=[],
            conclusion="c",
            bullets=["视频链接: https://www.bilibili.com/video/BV1HpP5zBEEp", "要点"],
            evidence_quotes=[],
            coverage="partial",
            confidence="medium",
            note_tags=[],
            follow_up_actions=[],
        )
        self.assertIn("值不值得回看", _brief_value_line(summary))

    def test_docs_link_lines_are_not_treated_as_video(self) -> None:
        ingest = IngestRequest(
            chat_id="-1",
            reply_to_message_id="1",
            request_id="tg-test-docs",
            source_kind="url",
            source_url="https://docs.openclaw.ai/",
            raw_text="https://docs.openclaw.ai/",
        )
        summary = SummaryResult(
            title="OpenClaw 安装指南",
            primary_topic="OpenClaw",
            secondary_topics=["AI代理"],
            entities=[],
            conclusion="OpenClaw 提供跨多个平台的 AI 代理服务，安装过程简单明了。",
            bullets=[
                "关键链接: https://docs.openclaw.ai/",
                "支持 WhatsApp、Telegram、Discord、iMessage 等平台的 AI 代理",
                "安装服务并配对 WhatsApp 以启动网关",
            ],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
            recommendation_level="must_read",
        )
        self.assertEqual(_what_is_it_line(ingest, summary), "OpenClaw 是一个跨平台 AI 代理网关，这条是它的官方安装文档。")
        self.assertIn("官方文档最省事", _worth_it_line(ingest, summary))
        self.assertIn("告诉你 OpenClaw 是什么", _why_it_matters_line(ingest, summary))

    def test_jarvis_lines_have_butler_style(self) -> None:
        ingest = IngestRequest(
            chat_id="-1",
            reply_to_message_id="1",
            request_id="tg-test-jarvis",
            source_kind="url",
            source_url="https://docs.openclaw.ai/",
            raw_text="https://docs.openclaw.ai/",
        )
        summary = SummaryResult(
            title="OpenClaw 安装指南",
            primary_topic="OpenClaw",
            secondary_topics=[],
            entities=[],
            conclusion="OpenClaw 提供跨多个平台的 AI 代理服务，安装过程简单明了。",
            bullets=["关键链接: https://docs.openclaw.ai/", "支持多平台", "安装服务并配对 WhatsApp"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=["访问官方文档开始安装"],
            reader_judgment="如果准备第一次上手，这条文档值得直接看。",
        )
        self.assertEqual(_jarvis_intro_line(), "Sir，已处理完毕。")
        self.assertIn("官方安装文档", _jarvis_brief_line(ingest, summary))
        self.assertIn("如果准备第一次上手", _jarvis_judgment_line(ingest, summary))


if __name__ == "__main__":
    unittest.main()
