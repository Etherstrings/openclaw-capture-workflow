import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.config import VideoSummaryConfig
from openclaw_capture_workflow.models import EvidenceBundle, EvidenceItem, SummaryResult
from openclaw_capture_workflow.video_experiment_summarizer import EvidenceDrivenVideoSummarizer


class _StubClient:
    def __init__(self) -> None:
        self.payloads = []

    def request_json_payload(self, system_prompt, payload):
        self.payloads.append(payload)
        if "chunk_index" in payload:
            return json.dumps(
                {
                    "start": payload.get("chunk_start"),
                    "end": payload.get("chunk_end"),
                    "heading": f"片段{payload['chunk_index']}",
                    "summary": f"总结{payload['chunk_index']}",
                    "key_points": [f"要点{payload['chunk_index']}"],
                    "evidence_quotes": payload.get("chunk_lines", [])[:1],
                    "uncertainties": [],
                },
                ensure_ascii=False,
            )
        if "transcript_excerpt" in payload:
            return json.dumps(
                {
                    "finance_matrix": [
                        {
                            "name": "中国食品",
                            "sector": "消费/饮料",
                            "thesis": "估值偏低，现金充足，产品矩阵稳定。",
                            "position_change": "资金从海底捞切换至此，继续持有。",
                            "risk": "节奏偏慢，但估值优势明显。",
                        },
                        {
                            "name": "海底捞",
                            "sector": "消费/餐饮",
                            "thesis": "业绩恢复，但估值已不便宜。",
                            "position_change": "剩余 2500 股，计划冲高卖出。",
                            "risk": "估值扩张空间有限。",
                        },
                    ],
                    "finance_snapshot": {
                        "market_view": ["市场赚钱效应一般，但港股结构尚可。"],
                        "performance_review": ["今年组合跑赢恒指与恒科。"],
                        "action_plan": ["后续以低频调仓为主，不追高。"],
                    },
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "title": "长视频总结",
                "primary_topic": "视频",
                "secondary_topics": [],
                "entities": [],
                "conclusion": "视频按时间顺序讲了两个主要部分。",
                "bullets": ["1. 前半段讲环境准备", "2. 后半段讲实际运行"],
                "evidence_quotes": [],
                "coverage": "full",
                "confidence": "high",
                "note_tags": [],
                "follow_up_actions": [],
                "timeliness": "medium",
                "effectiveness": "high",
                "recommendation_level": "recommended",
                "reader_judgment": "值得保留，后续回看时可以按时间段复核。",
                "outcome": "summarized",
                "evidence_basis": ["speech_tracks=asr"],
                "timeline_sections": [
                    {"start": 0.0, "end": 180.0, "heading": "片段1", "summary": "总结1", "bullets": ["要点1"], "evidence": ["[00:00] 第一段"]},
                    {"start": 150.0, "end": 330.0, "heading": "片段2", "summary": "总结2", "bullets": ["要点2"], "evidence": ["[03:00] 第二段"]},
                ],
                "uncertainties": [],
                "refusal_reason": "",
                "finance_matrix": [],
                "finance_snapshot": {},
            },
            ensure_ascii=False,
        )


class _FailingClient:
    def request_json_payload(self, system_prompt, payload):
        raise RuntimeError("gemini payload request failed: HTTP Error 403: Forbidden")


class EvidenceDrivenVideoSummarizerTest(unittest.TestCase):
    def test_summary_result_from_json_keeps_string_uncertainty_whole(self) -> None:
        summary = SummaryResult.from_json(
            json.dumps(
                {
                    "title": "测试视频",
                    "primary_topic": "视频",
                    "secondary_topics": [],
                    "entities": [],
                    "conclusion": "主题正确。",
                    "bullets": ["1. 第一条", "2. 第二条"],
                    "evidence_quotes": [],
                    "coverage": "partial",
                    "confidence": "medium",
                    "note_tags": [],
                    "follow_up_actions": [],
                    "outcome": "partial",
                    "evidence_basis": "speech_tracks=asr",
                    "timeline_sections": [],
                    "uncertainties": "转录文本质量极差",
                    "refusal_reason": "",
                },
                ensure_ascii=False,
            )
        )
        self.assertEqual(summary.evidence_basis, ["speech_tracks=asr"])
        self.assertEqual(summary.uncertainties, ["转录文本质量极差"])

    def test_chunked_video_summary_preserves_timeline_sections(self) -> None:
        config = VideoSummaryConfig(
            provider="aihubmix_gemini",
            transport="openai_compat",
            api_base_url="https://aihubmix.com/v1",
            api_key="key",
            model="gemini-2.5-pro",
            fallback_model="gemini-2.5-flash",
            timeout_seconds=60,
            chunk_window_seconds=180,
            chunk_overlap_seconds=30,
        )
        stub = _StubClient()
        summarizer = EvidenceDrivenVideoSummarizer(config, client=stub)  # type: ignore[arg-type]
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://example.com/video",
            platform_hint="video",
            title="长视频",
            text="长视频转写",
            evidence_type="multimodal_video",
            coverage="full",
            transcript="长视频转写",
            evidence_items=[
                EvidenceItem(
                    modality="speech",
                    source="asr",
                    provider="video_audio_command",
                    text="第一段",
                    timestamp_start=0.0,
                    timestamp_end=170.0,
                    confidence=0.9,
                    is_primary=True,
                ),
                EvidenceItem(
                    modality="speech",
                    source="asr",
                    provider="video_audio_command",
                    text="第二段",
                    timestamp_start=200.0,
                    timestamp_end=320.0,
                    confidence=0.9,
                    is_primary=True,
                ),
            ],
        )
        summary = summarizer.summarize(evidence)
        self.assertEqual(summary.outcome, "summarized")
        self.assertEqual(len(summary.timeline_sections), 2)
        self.assertEqual(summary.timeline_sections[0]["heading"], "片段1")
        self.assertEqual(summary.timeline_sections[1]["heading"], "片段2")
        self.assertEqual(summary.timeline_sections[0]["bullets"], ["要点1"])
        chunk_payloads = [payload for payload in stub.payloads if "chunk_index" in payload]
        self.assertGreaterEqual(len(chunk_payloads), 2)

    def test_enumeration_video_marks_partial_when_retained_points_too_few(self) -> None:
        config = VideoSummaryConfig(
            provider="aihubmix_gemini",
            transport="openai_compat",
            api_base_url="https://aihubmix.com/v1",
            api_key="key",
            model="gemini-2.5-pro",
            fallback_model="gemini-2.5-flash",
            timeout_seconds=60,
            chunk_window_seconds=180,
            chunk_overlap_seconds=30,
        )
        stub = _StubClient()
        summarizer = EvidenceDrivenVideoSummarizer(config, client=stub)  # type: ignore[arg-type]
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://example.com/video",
            platform_hint="video",
            title="面试中最常见的12个问题",
            text="问题一 请做自我介绍\n问题二 你的业余爱好是什么",
            evidence_type="multimodal_video",
            coverage="full",
            transcript="问题一 请做自我介绍\n问题二 你的业余爱好是什么",
            evidence_items=[
                EvidenceItem(
                    modality="speech",
                    source="asr",
                    provider="video_audio_command",
                    text="问题一 请做自我介绍\n问题二 你的业余爱好是什么",
                    timestamp_start=0.0,
                    timestamp_end=100.0,
                    confidence=0.9,
                    is_primary=True,
                ),
            ],
        )
        summary = summarizer.summarize(evidence)
        self.assertEqual(summary.outcome, "partial")
        self.assertTrue(any("仅覆盖" in item for item in summary.uncertainties))

    def test_dense_video_uses_smaller_chunk_window_for_completeness(self) -> None:
        config = VideoSummaryConfig(
            provider="aihubmix_gemini",
            transport="openai_compat",
            api_base_url="https://aihubmix.com/v1",
            api_key="key",
            model="gemini-2.5-pro",
            fallback_model="gemini-2.5-flash",
            timeout_seconds=60,
            chunk_window_seconds=180,
            chunk_overlap_seconds=30,
        )
        stub = _StubClient()
        summarizer = EvidenceDrivenVideoSummarizer(config, client=stub)  # type: ignore[arg-type]
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://example.com/video",
            platform_hint="video",
            title="面试中最常见的12个问题",
            text="\n".join([f"问题{i} 内容更完整一些" for i in range(1, 13)]),
            evidence_type="multimodal_video",
            coverage="full",
            transcript="\n".join([f"问题{i} 内容更完整一些" for i in range(1, 13)]),
            metadata={"video_duration_seconds": 720.0},
            evidence_items=[
                EvidenceItem(
                    modality="speech",
                    source="asr",
                    provider="video_audio_command",
                    text=f"问题{i} 内容更完整一些",
                    timestamp_start=float((i - 1) * 55),
                    timestamp_end=float((i - 1) * 55 + 35),
                    confidence=0.9,
                    is_primary=True,
                )
                for i in range(1, 13)
            ],
        )
        summarizer.summarize(evidence)
        final_payload = next(payload for payload in stub.payloads if "chunk_summaries" in payload)
        self.assertIn("chunk_window_seconds=90", final_payload["evidence_basis"])

    def test_finance_video_extracts_matrix_and_snapshot(self) -> None:
        config = VideoSummaryConfig(
            provider="aihubmix_gemini",
            transport="openai_compat",
            api_base_url="https://aihubmix.com/v1",
            api_key="key",
            model="gemini-2.5-pro",
            fallback_model="gemini-2.5-flash",
            timeout_seconds=60,
            chunk_window_seconds=180,
            chunk_overlap_seconds=30,
        )
        stub = _StubClient()
        summarizer = EvidenceDrivenVideoSummarizer(config, client=stub)  # type: ignore[arg-type]
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1unw9zxEHF",
            platform_hint="bilibili",
            title="第1143日投资记录",
            text="市场赚钱效应一般，海底捞、中国食品、腾讯控股。",
            evidence_type="multimodal_video",
            coverage="full",
            transcript="当前市场赚钱效应一般，但港股结构尚可。海底捞剩余2500股，中国食品继续持有。",
            metadata={
                "video_direction_estimate": {"kind": "finance_market"},
                "video_duration_seconds": 1760.0,
            },
            evidence_items=[
                EvidenceItem(
                    modality="speech",
                    source="asr",
                    provider="video_audio_command",
                    text="当前市场赚钱效应一般，但港股结构尚可。",
                    timestamp_start=0.0,
                    timestamp_end=170.0,
                    confidence=0.9,
                    is_primary=True,
                ),
                EvidenceItem(
                    modality="speech",
                    source="asr",
                    provider="video_audio_command",
                    text="海底捞剩余2500股，中国食品继续持有。",
                    timestamp_start=200.0,
                    timestamp_end=320.0,
                    confidence=0.9,
                    is_primary=True,
                ),
            ],
        )
        summary = summarizer.summarize(evidence)
        self.assertEqual(summary.finance_matrix[0]["name"], "中国食品")
        self.assertIn("市场赚钱效应一般", summary.finance_snapshot["market_view"][0])
        self.assertTrue(any("市场赚钱效应一般" in item for item in summary.bullets))
        self.assertTrue(any("中国食品" in item or "海底捞" in item for item in summary.bullets))
        self.assertNotEqual(summary.finance_matrix[0]["thesis"], summary.finance_matrix[0]["position_change"])
        self.assertNotEqual(summary.finance_matrix[0]["thesis"], summary.finance_matrix[0]["risk"])
        finance_payload = next(payload for payload in stub.payloads if "transcript_excerpt" in payload)
        self.assertEqual(finance_payload["source_url"], "https://www.bilibili.com/video/BV1unw9zxEHF")

    def test_model_unavailable_falls_back_to_local_chunk_briefing(self) -> None:
        config = VideoSummaryConfig(
            provider="aihubmix_gemini",
            transport="openai_compat",
            api_base_url="https://aihubmix.com/v1",
            api_key="key",
            model="gemini-2.5-pro",
            fallback_model="gemini-2.5-flash",
            timeout_seconds=60,
            chunk_window_seconds=180,
            chunk_overlap_seconds=30,
        )
        summarizer = EvidenceDrivenVideoSummarizer(config, client=_FailingClient())  # type: ignore[arg-type]
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://example.com/video",
            platform_hint="video",
            title="第1144日投资记录",
            text="投资体系、市场表现、海底捞与中国食品、后续计划。",
            evidence_type="multimodal_video",
            coverage="full",
            transcript="投资体系、市场表现、海底捞与中国食品、后续计划。",
            metadata={
                "video_duration_seconds": 1200.0,
                "video_direction_estimate": {"kind": "finance_market", "confidence": "high"},
                "transcript_timeline_lines": [
                    "[00:10] 先讲投资体系和普通投资者不要搞鄙视链。",
                    "[03:20] 市场赚钱效应一般，但组合回撤控制得更好。",
                    "[06:40] 中国食品估值更便宜，继续持有。",
                    "[08:10] 海底捞估值不便宜，计划冲高卖出。",
                    "[12:30] 后续以低频调仓为主，不追高。",
                ],
            },
            evidence_items=[
                EvidenceItem(
                    modality="speech",
                    source="asr",
                    provider="video_audio_command",
                    text="先讲投资体系和普通投资者不要搞鄙视链。",
                    timestamp_start=10.0,
                    timestamp_end=120.0,
                    confidence=0.9,
                    is_primary=True,
                ),
                EvidenceItem(
                    modality="speech",
                    source="asr",
                    provider="video_audio_command",
                    text="市场赚钱效应一般，但组合回撤控制得更好。",
                    timestamp_start=200.0,
                    timestamp_end=320.0,
                    confidence=0.9,
                    is_primary=True,
                ),
                EvidenceItem(
                    modality="speech",
                    source="asr",
                    provider="video_audio_command",
                    text="中国食品估值更便宜，继续持有。",
                    timestamp_start=400.0,
                    timestamp_end=520.0,
                    confidence=0.9,
                    is_primary=True,
                ),
                EvidenceItem(
                    modality="speech",
                    source="asr",
                    provider="video_audio_command",
                    text="海底捞估值不便宜，计划冲高卖出。",
                    timestamp_start=520.0,
                    timestamp_end=640.0,
                    confidence=0.9,
                    is_primary=True,
                ),
                EvidenceItem(
                    modality="speech",
                    source="asr",
                    provider="video_audio_command",
                    text="后续以低频调仓为主，不追高。",
                    timestamp_start=740.0,
                    timestamp_end=860.0,
                    confidence=0.9,
                    is_primary=True,
                ),
            ],
        )
        summary = summarizer.summarize(evidence)
        self.assertEqual(summary.outcome, "partial")
        self.assertGreaterEqual(len(summary.timeline_sections), 3)
        self.assertTrue(any("fallback=chunk_summaries" in item for item in summary.evidence_basis))
        self.assertTrue(any(item["name"] == "中国食品" for item in summary.finance_matrix))
        self.assertTrue(any(item["name"] == "海底捞" for item in summary.finance_matrix))
        self.assertIn("market_view", summary.finance_snapshot)
        self.assertIn("action_plan", summary.finance_snapshot)
        self.assertNotIn("已按时间段整理出视频主线", summary.conclusion)
        self.assertTrue(any("市场赚钱效应一般" in item or "海底捞" in item or "中国食品" in item for item in summary.bullets))
        self.assertFalse(any("search-card" in item or "vd_source" in item for item in summary.bullets))

    def test_model_unavailable_uses_metadata_timeline_lines_when_items_missing(self) -> None:
        config = VideoSummaryConfig(
            provider="aihubmix_gemini",
            transport="openai_compat",
            api_base_url="https://aihubmix.com/v1",
            api_key="key",
            model="gemini-2.5-pro",
            fallback_model="gemini-2.5-flash",
            timeout_seconds=60,
            chunk_window_seconds=180,
            chunk_overlap_seconds=30,
        )
        summarizer = EvidenceDrivenVideoSummarizer(config, client=_FailingClient())  # type: ignore[arg-type]
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://example.com/video",
            platform_hint="video",
            title="第1144日投资记录",
            text="投资体系、市场表现、海底捞与中国食品、后续计划。",
            evidence_type="multimodal_video",
            coverage="full",
            transcript="投资体系、市场表现、海底捞与中国食品、后续计划。",
            metadata={
                "video_duration_seconds": 1200.0,
                "video_direction_estimate": {"kind": "finance_market", "confidence": "high"},
                "transcript_timeline_lines": [
                    "[00:10] 先讲投资体系和普通投资者不要搞鄙视链。",
                    "[03:20] 市场赚钱效应一般，但组合回撤控制得更好。",
                    "[06:40] 中国食品估值更便宜，继续持有。",
                    "[08:10] 海底捞估值不便宜，计划冲高卖出。",
                    "[12:30] 后续以低频调仓为主，不追高。",
                ],
            },
        )
        summary = summarizer.summarize(evidence)
        self.assertGreaterEqual(len(summary.timeline_sections), 3)
        self.assertTrue(any("市场赚钱效应一般" in item or "低频调仓" in item for item in summary.bullets))

    def test_normalize_finance_matrix_dedupes_identical_fields(self) -> None:
        config = VideoSummaryConfig(
            provider="aihubmix_gemini",
            transport="openai_compat",
            api_base_url="https://aihubmix.com/v1",
            api_key="key",
            model="gemini-2.5-pro",
            fallback_model="gemini-2.5-flash",
            timeout_seconds=60,
            chunk_window_seconds=180,
            chunk_overlap_seconds=30,
        )
        summarizer = EvidenceDrivenVideoSummarizer(config, client=_StubClient())  # type: ignore[arg-type]
        rows = summarizer._normalize_finance_matrix(  # type: ignore[attr-defined]
            [
                {
                    "name": "海底捞",
                    "sector": "消费/餐饮",
                    "thesis": "海底捞估值不便宜，计划冲高卖出。",
                    "position_change": "海底捞估值不便宜，计划冲高卖出。",
                    "risk": "海底捞估值不便宜，计划冲高卖出。",
                }
            ]
        )
        self.assertEqual(rows[0]["thesis"], "海底捞估值不便宜")
        self.assertEqual(rows[0]["position_change"], "计划冲高卖出")
        self.assertEqual(rows[0]["risk"], "")

    def test_normalize_finance_snapshot_strips_spoken_fillers(self) -> None:
        config = VideoSummaryConfig(
            provider="aihubmix_gemini",
            transport="openai_compat",
            api_base_url="https://aihubmix.com/v1",
            api_key="key",
            model="gemini-2.5-pro",
            fallback_model="gemini-2.5-flash",
            timeout_seconds=60,
            chunk_window_seconds=180,
            chunk_overlap_seconds=30,
        )
        summarizer = EvidenceDrivenVideoSummarizer(config, client=_StubClient())  # type: ignore[arg-type]
        snapshot = summarizer._normalize_finance_snapshot(  # type: ignore[attr-defined]
            {
                "market_view": ["啊 ，市场赚钱效应一般，但港股结构尚可。"],
                "action_plan": ["啊 ，后续以低频调仓为主，不追高。"],
            }
        )
        self.assertEqual(snapshot["market_view"][0], "市场赚钱效应一般")
        self.assertEqual(snapshot["action_plan"][0], "后续以低频调仓为主")


if __name__ == "__main__":
    unittest.main()
