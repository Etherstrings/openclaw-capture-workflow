import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.config import VideoSummaryConfig
from openclaw_capture_workflow.models import EvidenceBundle
from openclaw_capture_workflow.video_experiment_summarizer import AiHubMixGeminiSummarizer


class _StubGeminiSummarizer(AiHubMixGeminiSummarizer):
    def __init__(self, config, responses):
        super().__init__(config)
        self.responses = list(responses)
        self.calls = []

    def _request(self, model: str, evidence: EvidenceBundle) -> str:
        self.calls.append(model)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class VideoExperimentSummarizerTest(unittest.TestCase):
    def test_falls_back_to_flash_when_pro_fails(self) -> None:
        config = VideoSummaryConfig(
            provider="aihubmix_gemini",
            transport="openai_compat",
            api_base_url="https://aihubmix.com/v1",
            api_key="key",
            model="gemini-2.5-pro",
            fallback_model="gemini-2.5-flash",
            timeout_seconds=60,
        )
        summarizer = _StubGeminiSummarizer(
            config,
            [
                RuntimeError("pro failed"),
                json.dumps(
                    {
                        "title": "视频总结",
                        "primary_topic": "视频",
                        "secondary_topics": [],
                        "entities": [],
                        "conclusion": "视频有两个层次点。",
                        "bullets": ["1. 第一层", "2. 第二层"],
                        "evidence_quotes": [],
                        "coverage": "full",
                        "confidence": "high",
                        "note_tags": [],
                        "follow_up_actions": [],
                    },
                    ensure_ascii=False,
                ),
            ],
        )
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://example.com/video",
            platform_hint="video",
            title="视频总结",
            text="1. 第一层\n2. 第二层",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={},
        )
        summary = summarizer.summarize(evidence)
        self.assertEqual(summary.title, "视频总结")
        self.assertEqual(summarizer.calls, ["gemini-2.5-pro", "gemini-2.5-flash"])


if __name__ == "__main__":
    unittest.main()

