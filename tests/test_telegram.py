import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.models import EvidenceBundle, IngestRequest, SummaryResult
from openclaw_capture_workflow.telegram import (
    TelegramNotifier,
    _brief_value_line,
    _extract_priority_project_lines,
    _one_line_summary,
    _render_ranked_rant_video_reply,
    _render_video_direct_reply,
    render_video_note_markdown,
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
        self.assertIn("回看原视频", _brief_value_line(summary))

    def test_build_result_message_payload_for_docs_uses_neutral_sections(self) -> None:
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
        payload = TelegramNotifier("token").build_result_message_payload(
            ingest,
            summary,
            "Inbox/OpenClaw/test.md",
            "",
            "obsidian://open",
        )
        self.assertIn("关键信息：", payload["text"])
        self.assertIn("判断：", payload["text"])
        self.assertIn("资料：", payload["text"])
        self.assertIn("https://docs.openclaw.ai/", payload["text"])
        self.assertNotIn("贾维斯", payload["text"])

    def test_build_result_message_payload_for_docs_overview_uses_direct_next_step_sentence(self) -> None:
        ingest = IngestRequest(
            chat_id="-1",
            reply_to_message_id="1",
            request_id="tg-test-docs-overview",
            source_kind="url",
            source_url="https://docs.openclaw.ai/",
            raw_text="https://docs.openclaw.ai/",
        )
        summary = SummaryResult(
            title="OpenClaw",
            primary_topic="OpenClaw",
            secondary_topics=[],
            entities=[],
            conclusion="当前拿到的是《OpenClaw》的概览页，不是完整安装文档。",
            bullets=[
                "平台支持: WhatsApp | Telegram | Discord | iMessage",
                "流程要点: Onboard and install the service | Pair WhatsApp and start the Gateway",
                "适用边界: 当前页更像文档首页概览，没有给出完整命令、验证和失败处理",
            ],
            evidence_quotes=[],
            coverage="full",
            confidence="medium",
            note_tags=[],
            follow_up_actions=["如果要真正开始安装，继续进入详细安装或配对子页查看具体命令和验证步骤。"],
            reader_judgment="当前页只够判断支持范围和大致接入方向，不够直接拿来安装。",
        )
        payload = TelegramNotifier("token").build_result_message_payload(
            ingest,
            summary,
            "Inbox/OpenClaw/test.md",
            "",
            "obsidian://open",
        )
        self.assertIn("如果要真正开始安装，继续进入详细安装或配对子页查看具体命令和验证步骤。", payload["text"])
        self.assertNotIn("建议先如果要真正开始安装", payload["text"])

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

    def test_build_result_message_payload_appends_model_and_elapsed_line(self) -> None:
        ingest = IngestRequest(
            chat_id="-1001",
            reply_to_message_id="42",
            request_id="tg-payload-meta",
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
            None,
            "gpt-4o-mini",
            12.34,
        )
        self.assertIn("使用gpt-4o-mini模型经过12.3秒总结完成。", payload["text"])

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
        self.assertIn("Milky 已完成视频总结", text)
        self.assertIn("🧊 关键做法", text)
        self.assertIn("💡 关键思路", text)
        self.assertIn("🔥 边界与风险", text)
        self.assertIn("系统会在开盘前给出逐只股票的分析和买入/持有建议", text)
        self.assertIn("记得随时呼叫Milky哦", text)
        self.assertNotIn("主要讲了这几件事：", text)
        self.assertNotIn("一句话总结：", text)
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
        self.assertIn("这条视频在盘点简中互联网里最常见的 10 类糟糕交互设计", text)
        self.assertIn("- 第10名：双击图片点赞", text)
        self.assertIn("- 第1名：shadowban / 幽灵屏蔽", text)
        self.assertIn("边界：", text)

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
        self.assertIn("Milky 已完成视频总结", text)
        self.assertIn("🧊 关键做法", text)
        self.assertIn("🔥 边界与风险", text)
        self.assertIn("世界地图上的信息面板", text)
        self.assertIn("自然灾害、天气预警、重点地区直播、金融资讯和宏观信息", text)
        self.assertIn("整个项目是开源免费的", text)
        self.assertNotIn("主要讲了这几件事：", text)

    def test_render_video_note_markdown_finance_prefers_normalized_opening_and_rows(self) -> None:
        summary = SummaryResult(
            title="第1144日投资记录",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="已按时间段整理出视频主线，可先用于快速筛选；细节仍建议回看原视频复核。",
            bullets=[
                "市场表现与组合回顾：投资它有两种维度的第一个就是哦我的收益率有多高第二个是我的回撤有多小",
            ],
            evidence_quotes=[],
            coverage="partial",
            confidence="medium",
            note_tags=[],
            follow_up_actions=[],
            reader_judgment="当前已经能看出视频主线，适合先看结构，再决定是否回看细节。",
            timeline_sections=[
                {
                    "start": 0.0,
                    "end": 300.0,
                    "heading": "持仓逻辑",
                    "summary": "重点对比海底捞和中国食品的估值与调仓思路。",
                    "bullets": ["海底捞估值不便宜。", "中国食品估值更低。"],
                    "evidence": [],
                }
            ],
            finance_matrix=[
                {
                    "name": "海底捞",
                    "sector": "消费/餐饮",
                    "thesis": "海底捞估值不便宜，计划冲高卖出。",
                    "position_change": "海底捞估值不便宜，计划冲高卖出。",
                    "risk": "海底捞估值不便宜，计划冲高卖出。",
                }
            ],
            finance_snapshot={
                "market_view": ["啊 ，市场赚钱效应一般，但港股结构尚可。"],
                "action_plan": ["啊 ，后续以低频调仓为主，不追高。"],
            },
        )
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1unw9zxEHF",
            platform_hint="bilibili",
            title="第1144日投资记录",
            text="市场赚钱效应一般，海底捞估值不便宜。",
            transcript="市场赚钱效应一般，海底捞估值不便宜。",
            evidence_type="multimodal_video",
            coverage="partial",
            metadata={
                "video_duration_seconds": 1800.0,
                "video_direction_estimate": {"kind": "finance_market"},
                "evidence_sources": ["video_audio_asr"],
            },
        )
        text = render_video_note_markdown(summary, evidence)
        self.assertIn("## 核心判断", text)
        self.assertIn("## 市场判断与后续计划", text)
        self.assertIn("## 标的矩阵", text)
        self.assertNotIn("已按时间段整理出视频主线", text)
        self.assertIn("后续以低频调仓为主", text)
        self.assertIn("| 海底捞 | 消费/餐饮 | 海底捞估值不便宜 | 计划冲高卖出 |  |", text)


if __name__ == "__main__":
    unittest.main()
