import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.models import EvidenceBundle, IngestRequest, SummaryResult
from openclaw_capture_workflow.telegram import (
    TelegramNotifier,
    _brief_value_line,
    _extract_priority_project_lines,
    _jarvis_brief_line,
    _jarvis_intro_line,
    _jarvis_judgment_line,
    _one_line_summary,
    _render_ranked_rant_video_reply,
    _render_video_direct_reply,
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

    def test_reply_lines_use_direct_style(self) -> None:
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
        self.assertEqual(_jarvis_intro_line(), "")
        self.assertIn("官方安装文档", _jarvis_brief_line(ingest, summary))
        self.assertEqual(_jarvis_judgment_line(ingest, summary), "如果准备第一次上手，这条文档值得直接看。")

    def test_build_result_message_payload_preserves_group_reply_context(self) -> None:
        ingest = IngestRequest(
            chat_id="-1001",
            reply_to_message_id="42",
            request_id="tg-payload-group",
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
        )
        payload = TelegramNotifier("token").build_result_message_payload(
            ingest,
            summary,
            "Inbox/OpenClaw/test.md",
            "",
            "obsidian://open",
        )
        self.assertEqual(payload["chat_id"], "-1001")
        self.assertEqual(payload["reply_to_message_id"], "42")
        self.assertIn("OpenClaw 安装指南", payload["text"])

    def test_build_result_message_payload_supports_direct_chat_without_reply(self) -> None:
        ingest = IngestRequest(
            chat_id="123456",
            reply_to_message_id=None,
            request_id="tg-payload-direct",
            source_kind="pasted_text",
            raw_text="给我总结一下这个项目。",
        )
        summary = SummaryResult(
            title="项目简报",
            primary_topic="项目",
            secondary_topics=[],
            entities=[],
            conclusion="这是一个项目简报。",
            bullets=["项目名称: example/repo", "GitHub地址: https://github.com/example/repo", "关键命令: /install-skill demo.skill"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=["执行命令：/install-skill demo.skill"],
        )
        payload = TelegramNotifier("token").build_result_message_payload(
            ingest,
            summary,
            "Inbox/OpenClaw/test.md",
            "",
            "obsidian://open",
        )
        self.assertEqual(payload["chat_id"], "123456")
        self.assertNotIn("reply_to_message_id", payload)
        self.assertIn("项目简报", payload["text"])

    def test_video_reply_matches_direct_answer_style(self) -> None:
        ingest = IngestRequest(
            chat_id="-1",
            reply_to_message_id="1",
            request_id="tg-test-video-direct",
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1bFPMzFEnd/",
        )
        summary = SummaryResult(
            title="OpenClaw你虾哥每天股票量化交易推荐",
            primary_topic="视频",
            secondary_topics=[],
            entities=["OpenClaw"],
            conclusion="视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议；同时流程是把自选股列表交给 OpenClaw，系统会在开盘前给出逐只股票的分析和买入/持有建议，整体更偏技术展示而非直接投资建议。",
            bullets=[
                "1. 视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议。",
                "2. 流程是把自选股列表交给 OpenClaw，系统会在开盘前给出逐只股票的分析和买入/持有建议。",
                "3. 实现上依赖 GitHub、服务器或自动化工作流，把整套分析流程持续跑起来。",
                "4. 视频明确提醒这更像技术展示和参考，不建议盲目跟单或直接照搬投资决策。",
                "5. 评论区一边在讨论信号准度，一边也拿回本和涨跌结果来检验这套方法是否靠谱。",
            ],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1bFPMzFEnd/",
            platform_hint="bilibili",
            title="OpenClaw你虾哥每天股票量化交易推荐",
            text="视频证据",
            transcript="把自选股交给 OpenClaw，在开盘前给出买入或者持有建议。",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={
                "evidence_sources": ["video_platform_metadata", "video_audio_asr"],
                "viewer_feedback": ["评论区有人讨论回本情况"],
                "video_story_blocks": [
                    {"label": "core_topic", "summary": "视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议。", "evidence": []},
                    {"label": "workflow", "summary": "流程是把自选股列表交给 OpenClaw，系统会在开盘前给出逐只股票的分析和买入/持有建议。", "evidence": []},
                    {"label": "implementation", "summary": "实现上依赖 GitHub、服务器或自动化工作流，把整套分析流程持续跑起来。", "evidence": []},
                    {"label": "risk", "summary": "视频明确提醒这更像技术展示和参考，不建议盲目跟单或直接照搬投资决策。", "evidence": []},
                ],
            },
        )
        text = _render_video_direct_reply(ingest, summary, evidence)
        self.assertIn("这个视频大意是在演示：作者怎么把 OpenClaw 改造成一个", text)
        self.assertIn("主要讲了这几件事：", text)
        self.assertIn("一句话总结：", text)
        self.assertIn("这视频是在秀一个 OpenClaw + 自动化工作流 的炒股辅助玩法", text)
        self.assertNotIn("归档：", text)
        self.assertNotIn("打开：", text)

    def test_ranked_rant_video_reply_matches_list_style(self) -> None:
        summary = SummaryResult(
            title="盘点简中互联网10大活全家交互设计，你肯定遇到过！",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="盘点简中互联网交互设计问题。",
            bullets=[],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        transcript = (
            "第十名双击图片点赞特别是小红书。"
            "第九名登录验证码死循环。"
            "第八名强行扫码登录。"
            "第七名下拉刷新结果进入二级抽屉。"
            "第六名各种 AI聊天板块。"
            "第五名应用类截图以为你要分享。"
            "第四名加入了短视频功能。"
            "第三名分享链接带有文字分享。"
            "第二名打开 App 立即刷新。"
            "第一名 shadowban 让你产生已经发出去了的错觉。"
        )
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1WAcQzKEW8/",
            platform_hint="bilibili",
            title="盘点简中互联网10大活全家交互设计，你肯定遇到过！",
            text=transcript,
            transcript=transcript,
            evidence_type="multimodal_video",
            coverage="full",
            metadata={"evidence_sources": ["video_platform_metadata", "video_audio_asr"]},
        )
        text = _render_ranked_rant_video_reply(summary, evidence)
        self.assertIn("这个视频在吐槽“简中互联网里最反人类的 10 种交互设计”", text)
        self.assertIn("第10名：双击图片点赞", text)
        self.assertIn("第1名：shadowban / 幽灵屏蔽", text)
        self.assertIn("整体风格就是高强度吐槽", text)

    def test_generic_tool_video_reply_is_more_natural_and_richer(self) -> None:
        ingest = IngestRequest(
            chat_id="-1",
            reply_to_message_id="1",
            request_id="tg-test-video-generic",
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1HpP5zBEEp/",
        )
        summary = SummaryResult(
            title="GitHub开源情报分析器，实时追踪全球热点",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="视频核心是在讲《GitHub开源情报分析器，实时追踪全球热点》的使用方法和落地流程；同时视频把关键流程拆成了输入、配置和运行几个环节，重点在把方案真正跑起来。",
            bullets=[
                "视频核心是在讲《GitHub开源情报分析器，实时追踪全球热点》的使用方法和落地流程。",
                "视频把关键流程拆成了输入、配置和运行几个环节，重点在把方案真正跑起来。",
                "实现上依赖 GitHub、服务器或自动化工作流，把整套分析流程持续跑起来。",
                "评论区主要围绕实盘体验、可靠性和使用边界展开讨论。",
            ],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1HpP5zBEEp/",
            platform_hint="bilibili",
            title="GitHub开源情报分析器，实时追踪全球热点",
            text="World Monitor 世界地图 自然灾害 天气预警 情报中心 开源免费 本地部署",
            transcript="就在前几天 GitHub 上开源了一套完整的全球实时监控系统。你可以在世界地图上看自然灾害、天气预警、重点地区直播、金融资讯和宏观信息。整个项目开源免费，也可以部署到本地，做成 24 小时运行的专属情报中心。",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={},
        )
        text = _render_video_direct_reply(ingest, summary, evidence)
        self.assertIn("主要讲了这几件事：", text)
        self.assertIn("世界地图上的信息面板", text)
        self.assertIn("自然灾害、天气预警、重点地区直播、金融资讯和宏观信息", text)
        self.assertIn("整个项目是开源免费的", text)
        self.assertIn("情报/监控面板的演示视频", text)


if __name__ == "__main__":
    unittest.main()
