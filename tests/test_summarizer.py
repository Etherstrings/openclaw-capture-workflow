import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.content_profile import infer_content_profile
from openclaw_capture_workflow.models import EvidenceBundle, SummaryResult
from openclaw_capture_workflow.summarizer import (
    PROMPT,
    _extract_explicit_video_outline,
    _signal_priority_bullets,
    _validate_and_normalize_summary,
)


class SummarizerPostprocessTest(unittest.TestCase):
    def test_prompt_uses_direct_analyst_style(self) -> None:
        self.assertIn("Direct Analyst", PROMPT)
        self.assertIn("direct, useful answer", PROMPT)
        self.assertNotIn("J.A.R.V.I.S.", PROMPT)
        self.assertNotIn("Tony Stark", PROMPT)
        self.assertNotIn("Sir", PROMPT)

    def test_signal_facts_are_prioritized_and_generic_bullets_removed(self) -> None:
        evidence = EvidenceBundle(
            source_kind="url",
            source_url="https://www.xiaohongshu.com/explore/abc",
            platform_hint="xiaohongshu",
            title="推荐一个 Skill",
            text="推荐一个 Skill",
            evidence_type="visible_page_text",
            coverage="full",
            metadata={
                "signals": {
                    "projects": ["star23/Day1Global-Skills"],
                    "links": ["https://github.com/star23/Day1Global-Skills"],
                    "skills": ["美股财报深度分析 Skill"],
                    "skill_ids": ["tech-earnings-deepdive"],
                }
            },
        )
        summary = SummaryResult(
            title="推荐一个 Skill",
            primary_topic="技能推荐",
            secondary_topics=[],
            entities=[],
            conclusion="该证据提供了完整信息。",
            bullets=["该证据提供了完整信息。", "适用于开发者和爱好者。", "安装方法很简单。"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertTrue(any("项目名称" in item for item in normalized.bullets))
        self.assertTrue(any("GitHub地址" in item for item in normalized.bullets))
        self.assertFalse(any("该证据提供了完整信息" in item for item in normalized.bullets))
        self.assertIn("star23/Day1Global-Skills", normalized.conclusion)

    def test_title_is_cleaned_from_github_ui_prefix(self) -> None:
        evidence = EvidenceBundle(
            source_kind="url",
            source_url="https://github.com/star23/Day1Global-Skills",
            platform_hint="github",
            title="GitHub - star23/Day1Global-Skills",
            text="证据",
            evidence_type="structured_github_text",
            coverage="full",
            metadata={},
        )
        summary = SummaryResult(
            title="GitHub - star23/Day1Global-Skills",
            primary_topic="技能推荐",
            secondary_topics=[],
            entities=[],
            conclusion="识别到项目 star23/Day1Global-Skills。",
            bullets=["项目名称: star23/Day1Global-Skills", "GitHub地址: https://github.com/star23/Day1Global-Skills"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertEqual(normalized.title, "star23/Day1Global-Skills")

    def test_title_removes_bilibili_suffix(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1HpP5zBEEp",
            platform_hint="bilibili",
            title="演示视频_哔哩哔哩_bilibili",
            text="证据",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={},
        )
        summary = SummaryResult(
            title="演示视频_哔哩哔哩_bilibili",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="已提取核心事实。",
            bullets=["要点1", "要点2", "要点3"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertEqual(normalized.title, "演示视频")

    def test_video_story_blocks_replace_raw_asr_fragments(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1bFPMzFEnd",
            platform_hint="bilibili",
            title="OpenClaw你虾哥每天股票量化交易推荐",
            text="视频证据",
            transcript="今天给大家介绍一个用龙虾做的一件事情最后会在每天早上开盘之前给你一个自选股分析",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={
                "video_story_blocks": [
                    {
                        "label": "core_topic",
                        "summary": "视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议。",
                        "evidence": ["标题: OpenClaw你虾哥每天股票量化交易推荐"],
                    },
                    {
                        "label": "workflow",
                        "summary": "流程是把自选股列表交给 OpenClaw，系统会在开盘前给出逐只股票的分析和买入/持有建议。",
                        "evidence": ["[00:16] 最后会在每天早上开盘之前"],
                    },
                    {
                        "label": "risk",
                        "summary": "视频明确提醒这更像技术展示和参考，不建议盲目跟单或直接照搬投资决策。",
                        "evidence": ["简介: 图一乐 别真跟着买 当然我跟着买了"],
                    },
                ]
            },
        )
        summary = SummaryResult(
            title="OpenClaw你虾哥每天股票量化交易推荐",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="视频介绍了一个流程。",
            bullets=[
                "1. 今天给大家介绍一个用龙虾做的一件事情最后会在每天早上开盘之前给你一个自选股分析",
                "2. 给你一个当天的自选股分析并给出建议",
                "3. 图一乐别真跟着买",
            ],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertEqual(
            normalized.bullets,
            [
                "1. 视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议。",
                "2. 流程是把自选股列表交给 OpenClaw，系统会在开盘前给出逐只股票的分析和买入/持有建议。",
                "3. 视频明确提醒这更像技术展示和参考，不建议盲目跟单或直接照搬投资决策。",
            ],
        )

    def test_video_story_blocks_do_not_invent_viewer_feedback(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1bFPMzFEnd",
            platform_hint="bilibili",
            title="OpenClaw你虾哥每天股票量化交易推荐",
            text="视频证据",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={
                "viewer_feedback": [],
                "video_story_blocks": [
                    {"label": "core_topic", "summary": "视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议。", "evidence": []},
                    {"label": "workflow", "summary": "流程是把自选股列表交给 OpenClaw，系统会在开盘前给出逐只股票的分析和买入/持有建议。", "evidence": []},
                    {"label": "risk", "summary": "视频明确提醒这更像技术展示和参考，不建议盲目跟单或直接照搬投资决策。", "evidence": []},
                ],
            },
        )
        summary = SummaryResult(
            title="OpenClaw你虾哥每天股票量化交易推荐",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="视频介绍了一个流程。",
            bullets=["无关紧要", "另一个无关紧要", "第三个无关紧要"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertEqual(len(normalized.bullets), 3)
        self.assertFalse(any("评论区" in item or "观众" in item for item in normalized.bullets))

    def test_full_interview_video_keeps_on_topic_bullets(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1ggpcevEgk",
            platform_hint="bilibili",
            title="求职面试中，最常见的12个问题",
            text="问题一 请做自我介绍\n问题二 你的业余爱好是什么\n问题三 谈谈你的缺点",
            transcript="问题一 请做自我介绍\n问题二 你的业余爱好是什么\n问题三 谈谈你的缺点\n问题四 为什么想进入我们公司",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={"video_duration_seconds": 453.0},
        )
        summary = SummaryResult(
            title="求职面试中，最常见的12个问题",
            primary_topic="求职面试常见问题及应对策略",
            secondary_topics=["自我介绍", "职业规划"],
            entities=[],
            conclusion="该视频主要讲面试中的常见问题和对应回答策略。",
            bullets=[
                "问题一聚焦自我介绍，建议控制在三分钟内并紧扣岗位相关经验。",
                "问题二强调业余爱好要体现积极性和个人特点，避免给面试官减分。",
                "问题三说明缺点回答要切合岗位要求，避免把明显优点硬说成缺点。",
            ],
            evidence_quotes=[],
            coverage="partial",
            confidence="medium",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        joined = "\n".join(normalized.bullets)
        self.assertIn("自我介绍", joined)
        self.assertIn("业余爱好", joined)
        self.assertNotIn("输入、配置和运行", joined)
        self.assertNotIn("行情、业绩和多种数据源", joined)
        self.assertNotIn("盲目跟单", joined)

    def test_video_uses_timeline_sections_when_model_bullets_are_fragmented(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1sMwEzmETo",
            platform_hint="bilibili",
            title="第1143日投资记录",
            text="市场赚钱效应 当前系统性风险 宁德时代 中国食品 海底捞",
            transcript="当前市场赚钱效应一般，但港股结构尚可。宁德时代估值偏离大，中国食品继续持有，海底捞等待波动。",
            evidence_type="multimodal_video",
            coverage="partial",
            metadata={"quality_gate": {"expected_outline_count": 19, "outcome": "partial", "mode": "full"}},
        )
        summary = SummaryResult(
            title="第1143日投资记录",
            primary_topic="投资记录",
            secondary_topics=[],
            entities=[],
            conclusion="视频记录了投资者当日的市场判断与持仓策略；当前已覆盖 6/19 项，可先用于快速判断，但不能当作完整版本。",
            bullets=[
                "1. 33.337.search-card.all.click&vd_source=f879",
                "2. 3.7啊 ¥3.8是进了港五通之后的最低点",
                "3. 4.5卖的你早已经回到 ¥4.6了",
            ],
            evidence_quotes=[],
            coverage="partial",
            confidence="medium",
            note_tags=[],
            follow_up_actions=[],
            outcome="partial",
            timeline_sections=[
                {
                    "start": 0.0,
                    "end": 180.0,
                    "heading": "市场赚钱效应与核心资产分析",
                    "summary": "当前市场赚钱效应一般，港股结构尚可，但月线级别仍存在系统性风险。",
                    "evidence": [],
                },
                {
                    "start": 180.0,
                    "end": 360.0,
                    "heading": "个股逻辑与持仓操作",
                    "summary": "视频重点讨论了宁德时代、中国食品和海底捞等标的的估值、持有逻辑和交易计划。",
                    "evidence": [],
                },
            ],
            uncertainties=["无", "仅覆盖 6/19 项，不能当作完整枚举总结。"],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertEqual(
            normalized.bullets,
            [
                "市场赚钱效应与核心资产分析：当前市场赚钱效应一般，港股结构尚可，但月线级别仍存在系统性风险",
                "个股逻辑与持仓操作：视频重点讨论了宁德时代、中国食品和海底捞等标的的估值、持有逻辑和交易计划",
            ],
        )
        self.assertTrue(all("：" in item for item in normalized.bullets))
        self.assertFalse(any("33.337" in item or "¥4.6" in item for item in normalized.bullets))
        self.assertNotIn("无", normalized.uncertainties)

    def test_explicit_video_outline_does_not_use_summary_bullets_as_evidence(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1bFPMzFEnd",
            platform_hint="bilibili",
            title="OpenClaw你虾哥每天股票量化交易推荐",
            text="视频证据",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={},
        )
        points = _extract_explicit_video_outline(
            evidence,
            [
                "1. 视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议。",
                "2. 流程是把自选股列表交给 OpenClaw，系统会在开盘前给出逐只股票的分析和买入/持有建议。",
                "3. 视频明确提醒这更像技术展示和参考，不建议盲目跟单或直接照搬投资决策。",
            ],
        )
        self.assertEqual(points, [])

    def test_tutorial_follow_up_actions_are_enriched_from_commands(self) -> None:
        evidence = EvidenceBundle(
            source_kind="url",
            source_url="https://github.com/star23/Day1Global-Skills",
            platform_hint="github",
            title="安装说明",
            text="安装方法：在 Claude 对话中使用 /install-skill 命令并上传 .skill 文件",
            evidence_type="structured_github_text",
            coverage="full",
            metadata={
                "signals": {
                    "commands": ["/install-skill https://github.com/Day1Global/Day1Global-Skills/raw/main/tech-earnings-deepdive.skill"]
                }
            },
        )
        summary = SummaryResult(
            title="安装说明",
            primary_topic="技能推荐",
            secondary_topics=[],
            entities=[],
            conclusion="按步骤安装即可。",
            bullets=["技能ID: tech-earnings-deepdive", "项目名称: star23/Day1Global-Skills", "GitHub地址: https://github.com/star23/Day1Global-Skills"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertTrue(any("/install-skill" in item for item in normalized.follow_up_actions))
        self.assertTrue(any("安装方法" in item for item in normalized.bullets))

    def test_default_reader_judgment_fields_are_populated(self) -> None:
        evidence = EvidenceBundle(
            source_kind="url",
            source_url="https://docs.openclaw.ai/",
            platform_hint="docs",
            title="OpenClaw Docs",
            text="Onboard and install the service\nPair WhatsApp and start the Gateway",
            evidence_type="visible_page_text",
            coverage="full",
            metadata={"content_profile": {"kind": "installation_tutorial"}},
        )
        summary = SummaryResult(
            title="OpenClaw 安装",
            primary_topic="OpenClaw",
            secondary_topics=[],
            entities=[],
            conclusion="OpenClaw 支持安装。",
            bullets=["安装流程包括服务部署", "支持 WhatsApp 配对", "可作为 AI Agent 网关"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertEqual(normalized.timeliness, "medium")
        self.assertEqual(normalized.effectiveness, "medium")
        self.assertEqual(normalized.recommendation_level, "optional")
        self.assertEqual(normalized.reader_judgment, "适合直接当安装参考，但环境与验证步骤仍建议回原文核对。")

    def test_bullets_keep_long_github_links_and_key_terms(self) -> None:
        evidence = EvidenceBundle(
            source_kind="url",
            source_url=(
                "https://github.com/kubernetes/website/blob/main/content/en/docs/"
                "setup/production-environment/container-runtimes.md"
            ),
            platform_hint="github",
            title="container-runtimes.md",
            text=(
                "Both the kubelet and the container runtime need to use the same cgroup driver.\n"
                "文档链接: https://github.com/kubernetes/website/blob/main/content/en/docs/"
                "setup/production-environment/container-runtimes.md"
            ),
            evidence_type="structured_github_text",
            coverage="full",
            metadata={
                "signals": {
                    "projects": ["kubernetes/website"],
                    "links": [
                        "https://github.com/kubernetes/website",
                        "https://github.com/kubernetes/website/blob/main/content/en/docs/setup/production-environment/container-runtimes.md",
                    ],
                }
            },
        )
        summary = SummaryResult(
            title="Kubernetes容器运行时配置",
            primary_topic="Kubernetes",
            secondary_topics=[],
            entities=[],
            conclusion="需要统一cgroup driver配置。",
            bullets=["需要统一配置。", "建议使用systemd。", "参考官方文档。"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        corpus = "\n".join(normalized.bullets)
        self.assertIn(
            "https://github.com/kubernetes/website/blob/main/content/en/docs/setup/production-environment/container-runtimes.md",
            corpus,
        )
        self.assertIn("container runtime", corpus.lower())

    def test_signal_bullets_prefer_repo_link_over_raw_skill_link(self) -> None:
        evidence = EvidenceBundle(
            source_kind="pasted_text",
            source_url=None,
            platform_hint=None,
            title="Skill推荐",
            text="项目地址: https://github.com/star23/Day1Global-Skills",
            evidence_type="raw_text",
            coverage="full",
            metadata={
                "signals": {
                    "projects": ["Day1Global/Day1Global-Skills", "star23/Day1Global-Skills"],
                    "links": [
                        "https://github.com/Day1Global/Day1Global-Skills/raw/main/tech-earnings-deepdive.skill",
                        "https://github.com/star23/Day1Global-Skills",
                        "https://github.com/Day1Global/Day1Global-Skills",
                    ],
                    "skill_ids": ["tech-earnings-deepdive"],
                }
            },
        )
        summary = SummaryResult(
            title="Skill推荐",
            primary_topic="技能推荐",
            secondary_topics=[],
            entities=[],
            conclusion="可用于美股财报分析。",
            bullets=["技能ID: tech-earnings-deepdive", "安装后可直接提问", "支持财报分析"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertTrue(normalized.bullets[0].startswith("技能ID: tech-earnings-deepdive"))
        self.assertTrue(any(item.startswith("项目名称: star23/Day1Global-Skills") for item in normalized.bullets))
        self.assertTrue(any("https://github.com/star23/Day1Global-Skills" in item for item in normalized.bullets))

    def test_installation_signal_bullets_prioritize_hard_facts_before_resources(self) -> None:
        evidence = EvidenceBundle(
            source_kind="url",
            source_url="https://docs.openclaw.ai/",
            platform_hint="docs",
            title="OpenClaw 安装指南",
            text="安装与配对说明",
            evidence_type="visible_page_text",
            coverage="full",
            metadata={
                "content_profile": {"kind": "installation_tutorial"},
                "signals": {
                    "prerequisites": ["前置条件: 先安装服务并准备可用账号。"],
                    "commands": ["openclaw service install"],
                    "validation_actions": ["验证动作: 完成 WhatsApp 配对后再启动网关。"],
                    "supported_platforms": ["WhatsApp", "Telegram", "Discord", "iMessage"],
                    "boundaries": ["适用边界: 没有完成配对前不能启动网关。"],
                    "links": ["https://docs.openclaw.ai/"],
                    "projects": ["openclaw/openclaw"],
                },
            },
        )
        bullets = _signal_priority_bullets(evidence, limit=5)
        self.assertEqual(
            [item.split(":", 1)[0] for item in bullets],
            ["前置条件", "安装方法", "验证动作", "平台支持", "适用边界"],
        )

    def test_non_github_video_link_is_not_labeled_as_github(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            platform_hint="youtube",
            title="Video",
            text="视频来源于YouTube",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={"signals": {"links": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]}},
        )
        summary = SummaryResult(
            title="Video",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="这是一个视频。",
            bullets=["要点1", "要点2", "要点3"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        corpus = "\n".join(normalized.bullets)
        self.assertIn("视频链接: https://www.youtube.com/watch?v=dQw4w9WgXcQ", corpus)
        self.assertNotIn("GitHub地址: https://www.youtube.com/watch?v=dQw4w9WgXcQ", corpus)

    def test_video_gate_forces_partial_coverage_and_warning_in_conclusion(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1tyNNzxEpK",
            platform_hint="bilibili",
            title="测试视频",
            text="仅提取到有限关键帧文本。",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={"video_gate_reasons": ["missing speech track (subtitle/transcript)"]},
        )
        summary = SummaryResult(
            title="测试视频",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="提取完成。",
            bullets=["要点1", "要点2", "要点3"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertEqual(normalized.coverage, "partial")
        self.assertEqual(normalized.confidence, "medium")
        self.assertIn("证据不完整", normalized.conclusion)

    def test_generic_non_video_conclusion_is_rewritten_from_hard_facts(self) -> None:
        evidence = EvidenceBundle(
            source_kind="url",
            source_url="https://docs.openclaw.ai/",
            platform_hint="docs",
            title="OpenClaw 安装指南",
            text="OpenClaw supports WhatsApp and Telegram. Install the service, pair WhatsApp, then start the gateway.",
            evidence_type="visible_page_text",
            coverage="full",
            metadata={
                "content_profile": {"kind": "installation_tutorial"},
                "signals": {
                    "commands": ["openclaw service install"],
                    "validation_actions": ["完成 WhatsApp 配对后再启动网关。"],
                    "supported_platforms": ["WhatsApp", "Telegram"],
                    "boundaries": ["没有完成配对前不能启动网关。"],
                },
            },
        )
        summary = SummaryResult(
            title="OpenClaw 安装指南",
            primary_topic="OpenClaw",
            secondary_topics=[],
            entities=[],
            conclusion="已提取核心事实。",
            bullets=["支持多平台", "安装很简单", "适合作为参考"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertNotIn("已提取核心事实", normalized.conclusion)
        self.assertIn("关键动作", normalized.conclusion)
        self.assertIn("不能启动网关", normalized.conclusion)

    def test_docs_homepage_is_not_misclassified_as_full_install_tutorial(self) -> None:
        profile = infer_content_profile(
            "url",
            "https://docs.openclaw.ai/",
            "Any OS gateway for AI agents across WhatsApp, Telegram, Discord, iMessage, and more.\nOnboard and install the service\nPair WhatsApp and start the Gateway",
            {
                "signals": {"supported_platforms": ["WhatsApp", "Telegram", "Discord", "iMessage"]},
                "step_items": [
                    {"title": "Onboard and install the service", "detail": None, "command": None},
                    {"title": "Pair WhatsApp and start the Gateway", "detail": None, "command": None},
                ],
            },
        )
        self.assertEqual(profile["kind"], "general_capture")

    def test_docs_homepage_conclusion_admits_it_is_only_overview(self) -> None:
        evidence = EvidenceBundle(
            source_kind="url",
            source_url="https://docs.openclaw.ai/",
            platform_hint="docs",
            title="OpenClaw - OpenClaw",
            text="Any OS gateway for AI agents across WhatsApp, Telegram, Discord, iMessage, and more.\nOnboard and install the service\nPair WhatsApp and start the Gateway",
            evidence_type="visible_page_text",
            coverage="full",
            metadata={
                "signals": {"supported_platforms": ["WhatsApp", "Telegram", "Discord", "iMessage"]},
                "step_items": [
                    {"title": "Onboard and install the service", "detail": None, "command": None},
                    {"title": "Pair WhatsApp and start the Gateway", "detail": None, "command": None},
                ],
            },
        )
        summary = SummaryResult(
            title="OpenClaw 安装指南",
            primary_topic="OpenClaw",
            secondary_topics=[],
            entities=[],
            conclusion="已提取核心事实。",
            bullets=["支持 WhatsApp、Telegram、Discord、iMessage 等平台", "安装服务并配对 WhatsApp 以启动网关"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertIn("概览页", normalized.conclusion)
        self.assertIn("不是完整安装文档", normalized.conclusion)
        self.assertEqual(normalized.reader_judgment, "当前页只够判断支持范围和大致接入方向，不够直接拿来安装。")
        self.assertEqual(
            normalized.follow_up_actions,
            ["如果要真正开始安装，继续进入详细安装或配对子页查看具体命令和验证步骤。"],
        )

    def test_incomplete_video_prefers_evidence_backed_bullets_and_guidance_actions(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1tyNNzxEpK",
            platform_hint="bilibili",
            title="杀戮尖塔2 全英雄基础流派攻略",
            text="杀戮尖塔2 全英雄基础流派攻略\n新手必看小技巧，主要讲选牌与路线",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={
                "signals": {
                    "links": ["https://www.bilibili.com/video/BV1tyNNzxEpK"]
                },
                "video_gate_reasons": ["missing speech track (subtitle/transcript)"],
                "evidence_sources": ["video_page_snapshot"],
            },
        )
        summary = SummaryResult(
            title="杀戮尖塔2 全英雄基础流派攻略",
            primary_topic="游戏攻略",
            secondary_topics=[],
            entities=[],
            conclusion="这是一个完整深入的视频讲解。",
            bullets=[
                "视频链接: https://www.bilibili.com/video/BV1tyNNzxEpK",
                "视频包含全英雄基础流派的攻略",
                "素材未包含进阶内容",
            ],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=["观看视频以获取详细攻略"],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        joined = "\n".join(normalized.bullets)
        self.assertIn("https://www.bilibili.com/video/BV1tyNNzxEpK", joined)
        self.assertNotIn("素材未包含进阶内容", joined)
        self.assertIn("主题: 杀戮尖塔2 全英雄基础流派攻略", joined)
        self.assertTrue(any("补抓字幕或语音轨" in item for item in normalized.follow_up_actions))

    def test_complete_video_rewrites_generic_conclusion_and_filters_noisy_actions(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1y4411p74E",
            platform_hint="bilibili",
            title="视频演示如何玩转一个开源项目 |如何运行+如何读代码 |顺便讲讲IDEA和Spring Boot |Java/Python/C语言/C++项目均适用 |视频教程",
            text="重点看这个视频对我学项目有没有帮助。",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={
                "user_guidance": "重点看这个视频对我学项目有没有帮助。",
                "video_extraction_profile": "dry_run_probe",
                "signals": {
                    "links": ["https://www.bilibili.com/video/BV1y4411p74E"]
                },
            },
        )
        summary = SummaryResult(
            title="开源项目使用视频教程",
            primary_topic="开源项目",
            secondary_topics=[],
            entities=[],
            conclusion="已提取核心事实。",
            bullets=[
                "视频链接: https://www.bilibili.com/video/BV1y4411p74E",
                "了解项目功能和技术点是入手的第一步",
                "视频中介绍了如何下载和运行开源项目",
                "强调了对陌生项目的决心和学习兴趣",
                "视频时长约90秒，适合快速学习",
            ],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[
                "标题: 视频演示如何玩转一个开源项目 |如何运行+如何读代码 |顺便讲讲IDEA和Spring Boot |Java/Python/C语言/C++项目均适用 |视频教程",
                "[00:21] 但是我还是不太会运行它,跑不起来",
                "观看视频以获取详细步骤",
                "尝试下载并运行推荐的Hello项目",
                "记录学习过程中遇到的问题以便后续解决",
            ],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertEqual(
            normalized.conclusion,
            "视频的核心意思是：了解项目功能和技术点是入手的第一步；同时补充如何下载和运行开源项目。",
        )
        self.assertFalse(any("视频时长约90秒" in item for item in normalized.bullets))
        self.assertEqual(
            normalized.follow_up_actions,
            [
                "尝试下载并运行推荐的Hello项目",
                "记录学习过程中遇到的问题以便后续解决",
            ],
        )

    def test_complete_video_filters_comment_area_actions_and_prefers_real_next_steps(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1zCfdBqEbs",
            platform_hint="bilibili",
            title="Situation Monitor开源情报聚合面板实测",
            text="重点看这个项目值不值得学。",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={"user_guidance": "重点看这个项目值不值得学。"},
        )
        summary = SummaryResult(
            title="Situation Monitor开源情报聚合面板实测",
            primary_topic="Situation Monitor开源项目",
            secondary_topics=[],
            entities=[],
            conclusion="Situation Monitor项目适合需要聚合全球新闻、金融和地缘数据的用户，部署简单，值得关注和学习。",
            bullets=[
                "视频链接: https://www.bilibili.com/video/BV1zCfdBqEbs",
                "Situation Monitor为开源情报聚合面板，支持全球新闻、金融市场、地缘政治等多数据源实时可视化",
                "项目可通过TRAE或chain工具几分钟内一键本地部署，适合非技术用户快速上手",
                "支持自定义仪表盘和数据源，核心新闻数据源为GDELT等开放接口",
                "面板内容和界面为全英文，适合具备一定英文基础的用户",
            ],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[
                "访问视频评论区获取Situation Monitor的GitHub链接",
                "准备本地Python环境，确保依赖项齐全",
                "使用TRAE或chain工具复制项目URL并一键部署",
                "根据需求自定义仪表盘和数据源",
            ],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertFalse(any("评论区" in item for item in normalized.follow_up_actions))
        self.assertTrue(any("TRAE或chain" in item for item in normalized.follow_up_actions))
        self.assertTrue(any("仪表盘和数据源" in item for item in normalized.follow_up_actions))

    def test_signal_links_strip_tracking_params_in_bullets(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7?xsec_token=abc&xsec_source=pc_feed",
            platform_hint="xiaohongshu",
            title="小红书视频",
            text="正文",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={
                "signals": {
                    "links": [
                        "https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7?xsec_token=abc&xsec_source=pc_feed"
                    ]
                }
            },
        )
        summary = SummaryResult(
            title="小红书视频",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="完成。",
            bullets=["要点1", "要点2", "要点3"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        joined = "\n".join(normalized.bullets)
        self.assertIn("https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7", joined)
        self.assertNotIn("xsec_token=", joined)


if __name__ == "__main__":
    unittest.main()
