import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.models import EvidenceBundle, SummaryResult
from openclaw_capture_workflow.summarizer import _extract_enumerated_points_from_text, _validate_and_normalize_summary
from openclaw_capture_workflow.video_truth_eval import evaluate_enumeration_recall


class VideoTruthEvalTest(unittest.TestCase):
    def test_extract_enumerated_points_from_numbered_lines(self) -> None:
        text = "\n".join([f"{idx}. 第{idx}个关键点" for idx in range(1, 10)])
        points = _extract_enumerated_points_from_text(text)
        self.assertEqual(len(points), 9)
        self.assertEqual(points[0], "第1个关键点")
        self.assertEqual(points[-1], "第9个关键点")

    def test_extract_enumerated_points_from_chinese_numbering(self) -> None:
        text = "\n".join(["一、先做A", "二、再做B", "三、最后做C"])
        points = _extract_enumerated_points_from_text(text)
        self.assertEqual(points, ["先做A", "再做B", "最后做C"])

    def test_video_summary_preserves_all_detected_points(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://example.com/video",
            platform_hint="video",
            title="九个步骤视频",
            text="\n".join([f"{idx}. 第{idx}个关键点" for idx in range(1, 10)]),
            evidence_type="multimodal_video",
            coverage="full",
            metadata={},
        )
        summary = SummaryResult(
            title="九个步骤视频",
            primary_topic="步骤",
            secondary_topics=[],
            entities=[],
            conclusion="视频介绍了多个步骤。",
            bullets=["1. 第1个关键点", "2. 第2个关键点", "3. 第3个关键点"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertEqual(len(normalized.bullets), 9)
        self.assertTrue(normalized.bullets[8].startswith("9. "))

    def test_enumeration_recall_detects_missing_and_order(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://example.com/video",
            platform_hint="video",
            title="三步视频",
            text="\n".join(["1. 先做A", "2. 再做B", "3. 最后做C"]),
            evidence_type="multimodal_video",
            coverage="full",
            metadata={},
        )
        summary = SummaryResult(
            title="三步视频",
            primary_topic="步骤",
            secondary_topics=[],
            entities=[],
            conclusion="视频介绍了三步。",
            bullets=["1. 先做A", "3. 最后做C", "2. 再做B"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        recall = evaluate_enumeration_recall(evidence, summary)
        self.assertEqual(len(recall.detected_points), 3)
        self.assertEqual(len(recall.summary_points), 3)
        self.assertFalse(recall.order_preserved)
        self.assertEqual(recall.missing_points, [])
        self.assertTrue(recall.outline_detected)

    def test_enumeration_recall_detects_outline_from_structured_sections(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.youtube.com/watch?v=demo",
            platform_hint="youtube",
            title="章节型视频",
            text="正文",
            evidence_type="structured_document",
            coverage="full",
            metadata={
                "structured_document": {
                    "sections": [
                        {"heading": "生意壁垒与未来从政野望", "level": 2, "content": "Follow along using the transcript."},
                        {"heading": "普通人阶层跨越的人生方法论", "level": 2, "content": "Follow along using the transcript."},
                    ]
                }
            },
        )
        summary = SummaryResult(
            title="章节型视频",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="视频包含两个章节。",
            bullets=["1. 生意壁垒与未来从政野望", "2. 普通人阶层跨越的人生方法论"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        recall = evaluate_enumeration_recall(evidence, summary)
        self.assertTrue(recall.outline_detected)
        self.assertEqual(len(recall.detected_points), 2)
        self.assertTrue(recall.order_preserved)

    def test_enumeration_recall_hits_story_blocks(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1bFPMzFEnd",
            platform_hint="bilibili",
            title="OpenClaw你虾哥每天股票量化交易推荐",
            text="视频证据",
            transcript="视频证据",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={
                "viewer_feedback": ["评论区有人反馈信号有一定参考性"],
                "video_story_blocks": [
                    {"label": "core_topic", "summary": "视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议。", "evidence": []},
                    {"label": "workflow", "summary": "流程是把自选股列表交给 OpenClaw，系统会在开盘前给出逐只股票的分析和买入/持有建议。", "evidence": []},
                    {"label": "implementation", "summary": "系统会结合行情、业绩和多种数据源来做判断，而不只是给一句结论。", "evidence": []},
                    {"label": "risk", "summary": "视频明确提醒这更像技术展示和参考，不建议盲目跟单或直接照搬投资决策。", "evidence": []},
                    {"label": "viewer_feedback", "summary": "评论区主要围绕实盘体验、可靠性和使用边界展开讨论。", "evidence": ["评论区有人反馈信号有一定参考性"]},
                ],
            },
        )
        summary = SummaryResult(
            title="OpenClaw你虾哥每天股票量化交易推荐",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议。",
            bullets=[
                "1. 视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议。",
                "2. 流程是把自选股列表交给 OpenClaw，系统会在开盘前给出逐只股票的分析和买入/持有建议。",
                "3. 系统会结合行情、业绩和多种数据源来做判断，而不只是给一句结论。",
                "4. 视频明确提醒这更像技术展示和参考，不建议盲目跟单或直接照搬投资决策。",
                "5. 评论区主要围绕实盘体验、可靠性和使用边界展开讨论。",
            ],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        recall = evaluate_enumeration_recall(evidence, summary)
        self.assertTrue(recall.story_blocks_detected)
        self.assertEqual(recall.story_block_count, 4)
        self.assertTrue(recall.workflow_hit)
        self.assertTrue(recall.risk_hit)
        self.assertTrue(recall.viewer_feedback_hit)
        self.assertTrue(recall.bullet_quality_ok)

    def test_enumeration_recall_flags_raw_transcript_bullets(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1bFPMzFEnd",
            platform_hint="bilibili",
            title="OpenClaw你虾哥每天股票量化交易推荐",
            text="视频证据",
            transcript="今天给大家介绍一个用龙虾做的一件事情最后会在每天早上开盘之前给你一个当天自选股的分析并给出买入或者持有的建议",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={},
        )
        summary = SummaryResult(
            title="OpenClaw你虾哥每天股票量化交易推荐",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="视频介绍了一个流程。",
            bullets=[
                "1. 今天给大家介绍一个用龙虾做的一件事情最后会在每天早上开盘之前给你一个当天自选股的分析并给出买入或者持有的建议"
            ],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        recall = evaluate_enumeration_recall(evidence, summary)
        self.assertFalse(recall.bullet_quality_ok)

    def test_sparse_video_does_not_require_narrative_story_blocks(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1tyNNzxEpK",
            platform_hint="bilibili",
            title="杀戮尖塔2 全英雄基础流派攻略",
            text="杀戮尖塔2 全英雄基础流派攻略\n新手必看小技巧，主要讲选牌与路线",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={
                "signals": {"links": ["https://www.bilibili.com/video/BV1tyNNzxEpK"]},
                "video_gate_reasons": ["missing speech track (subtitle/transcript)"],
                "evidence_sources": ["video_page_snapshot"],
            },
        )
        summary = SummaryResult(
            title="杀戮尖塔2 全英雄基础流派攻略",
            primary_topic="游戏攻略",
            secondary_topics=[],
            entities=[],
            conclusion="视频主要介绍了选牌和路线。",
            bullets=[
                "视频链接: https://www.bilibili.com/video/BV1tyNNzxEpK",
                "主题: 杀戮尖塔2 全英雄基础流派攻略",
                "新手必看小技巧，主要讲选牌与路线",
            ],
            evidence_quotes=[],
            coverage="partial",
            confidence="medium",
            note_tags=[],
            follow_up_actions=[],
        )
        recall = evaluate_enumeration_recall(evidence, summary)
        self.assertFalse(recall.story_blocks_detected)
        self.assertFalse(recall.workflow_required)
        self.assertFalse(recall.risk_required)
        self.assertFalse(recall.viewer_feedback_available)


if __name__ == "__main__":
    unittest.main()
