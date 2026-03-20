import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.video_summary_score import score_video_summary


class VideoSummaryScoreTest(unittest.TestCase):
    def test_clear_summary_gets_high_score(self) -> None:
        score = score_video_summary(
            {
                "conclusion": "这条视频在讲面试中最常见的12个问题，并给出逐项回答策略。",
                "bullets": [
                    "先讲自我介绍要控制在三分钟内并贴近岗位经验。",
                    "再讲业余爱好与缺点的回答边界，避免模板化空话。",
                    "最后给出职业规划、薪资、加班等高频问题的应对框架。",
                ],
                "coverage": "full",
            },
            outcome="summarized",
            quality_gate={"outcome": "summarized"},
            status="done",
        )
        self.assertGreaterEqual(score.total, 7)
        self.assertGreaterEqual(score.topic_clarity, 2)

    def test_partial_summary_without_boundary_is_penalized(self) -> None:
        score = score_video_summary(
            {
                "conclusion": "视频主要讲投资策略。",
                "bullets": ["讲了很多观点", "还有一些案例"],
                "coverage": "partial",
                "uncertainties": [],
            },
            outcome="partial",
            quality_gate={"outcome": "partial"},
            status="done",
        )
        self.assertEqual(score.boundary_honesty, 0)
        self.assertTrue(any("partial 结果没有清晰标注证据边界" in item for item in score.notes))

    def test_partial_with_low_outline_retention_has_mainline_penalty(self) -> None:
        score = score_video_summary(
            {
                "conclusion": "视频在讲软件评测流程。",
                "bullets": ["测试环境准备", "跑分方法", "结果解读"],
                "coverage": "partial",
                "uncertainties": ["仅覆盖部分证据。"],
            },
            outcome="partial",
            quality_gate={"outcome": "partial", "expected_outline_count": 10, "retained_outline_count": 2},
            status="done",
        )
        self.assertLessEqual(score.mainline_coverage, 1)
        self.assertTrue(any("主线覆盖偏低" in item for item in score.notes))


if __name__ == "__main__":
    unittest.main()
