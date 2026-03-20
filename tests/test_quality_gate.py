import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.config import (
    AppConfig,
    ExtractorConfig,
    ObsidianConfig,
    SummarizerConfig,
    TelegramConfig,
)
from openclaw_capture_workflow.models import CaptureManifest, EvidenceBundle, EvidenceItem
from openclaw_capture_workflow.quality_gate import evaluate_quality_gate


def _config() -> AppConfig:
    return AppConfig(
        listen_host="127.0.0.1",
        listen_port=8765,
        state_dir="state",
        obsidian=ObsidianConfig(
            vault_path="/tmp",
            inbox_root="Inbox/OpenClaw",
            topics_root="Topics",
            entities_root="Entities",
            auto_topic_whitelist=["AI"],
            auto_topic_blocklist=["测试"],
            auto_entity_pages=False,
        ),
        telegram=TelegramConfig(result_bot_token="token"),
        summarizer=SummarizerConfig(api_base_url="https://example.com", api_key="k", model="m", timeout_seconds=30),
        extractors=ExtractorConfig(),
    )


class QualityGateTest(unittest.TestCase):
    def test_video_gate_refuses_without_speech_evidence(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://example.com/video",
            platform_hint="video",
            title="测试视频",
            text="[关键帧OCR]\n只有画面文字，没有语音",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={"tracks": {"has_subtitle": False, "has_transcript": False, "has_keyframes": True, "has_keyframe_ocr": True}},
            capture_manifest=CaptureManifest(),
        )
        gate = evaluate_quality_gate(evidence, _config())
        self.assertEqual(gate.outcome, "refused")
        self.assertTrue(any("missing speech evidence" in item for item in gate.reasons))

    def test_video_gate_marks_partial_when_coverage_is_unknown(self) -> None:
        speech_text = "这是一段真实转写。" * 80
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://example.com/video",
            platform_hint="video",
            title="测试视频",
            text=speech_text,
            evidence_type="multimodal_video",
            coverage="full",
            transcript=speech_text,
            metadata={
                "tracks": {"has_subtitle": False, "has_transcript": True, "has_keyframes": True, "has_keyframe_ocr": False},
                "video_extraction_profile": "dry_run_probe",
                "video_probe_seconds": 180,
            },
            capture_manifest=CaptureManifest(),
        )
        gate = evaluate_quality_gate(evidence, _config())
        self.assertEqual(gate.outcome, "partial")
        self.assertEqual(gate.mode, "probe")
        self.assertEqual(gate.probe_quality, "usable")
        self.assertTrue(any("probe" in item for item in gate.reasons))

    def test_video_gate_allows_summary_with_long_speech_and_coverage(self) -> None:
        speech_text = "这是一段覆盖较高的真实转写内容。" * 80
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://example.com/video",
            platform_hint="video",
            title="测试视频",
            text=speech_text,
            evidence_type="multimodal_video",
            coverage="full",
            transcript=speech_text,
            metadata={
                "tracks": {"has_subtitle": False, "has_transcript": True, "has_keyframes": True, "has_keyframe_ocr": True},
                "video_duration_seconds": 300,
            },
            evidence_items=[
                EvidenceItem(
                    modality="speech",
                    source="asr",
                    provider="video_audio_command",
                    text="这是一段覆盖较高的真实转写内容。" * 80,
                    timestamp_start=0.0,
                    timestamp_end=120.0,
                    confidence=0.9,
                    is_primary=True,
                )
            ],
            capture_manifest=CaptureManifest(),
        )
        gate = evaluate_quality_gate(evidence, _config())
        self.assertEqual(gate.outcome, "summarized")
        self.assertGreaterEqual(gate.coverage_ratio or 0.0, 0.35)

    def test_video_gate_marks_low_quality_english_as_theme_only_partial(self) -> None:
        gibberish = (
            "Foher securitylifeses morlst takenerountet but werepecial fiew fomo "
            "twonte malineargo the gena sive home the first humans marge "
        ) * 12
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://example.com/video",
            platform_hint="video",
            title="人类起源",
            text=gibberish,
            evidence_type="multimodal_video",
            coverage="full",
            transcript=gibberish,
            metadata={
                "tracks": {"has_subtitle": False, "has_transcript": True, "has_keyframes": True, "has_keyframe_ocr": True},
                "transcript_language": "zh_cn",
                "video_duration_seconds": 240,
            },
            evidence_items=[
                EvidenceItem(
                    modality="speech",
                    source="asr",
                    provider="video_audio_command",
                    text=gibberish,
                    timestamp_start=0.0,
                    timestamp_end=180.0,
                    confidence=0.9,
                    is_primary=True,
                )
            ],
            capture_manifest=CaptureManifest(),
        )
        gate = evaluate_quality_gate(evidence, _config())
        self.assertEqual(gate.outcome, "partial")
        self.assertEqual(gate.speech_quality, "bad")
        self.assertTrue(gate.theme_only)


if __name__ == "__main__":
    unittest.main()
