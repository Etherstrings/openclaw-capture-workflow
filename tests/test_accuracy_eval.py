import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.accuracy_eval import (
    EvalExpectation,
    StepScore,
    diagnose_root_cause,
    evaluate_extract_step,
    evaluate_note_step,
    evaluate_summary_step,
    render_markdown_report,
)
from openclaw_capture_workflow.models import EvidenceBundle, SummaryResult


class AccuracyEvalTest(unittest.TestCase):
    def test_extract_step_detects_missing_expected_keyword(self) -> None:
        evidence = EvidenceBundle(
            source_kind="url",
            source_url="https://example.com",
            platform_hint="web",
            title="t",
            text="这是一段正文，只提到了 OpenClaw，没有提到目标技能ID。",
            evidence_type="visible_page_text",
            coverage="full",
            metadata={},
        )
        expect = EvalExpectation(required_keywords=["tech-earnings-deepdive"], min_evidence_chars=20)
        step = evaluate_extract_step(evidence, expect)
        self.assertFalse(step.passed)
        self.assertTrue(any("tech-earnings-deepdive" in item for item in step.missing))

    def test_summary_step_blocks_generic_phrase(self) -> None:
        summary = SummaryResult(
            title="标题",
            primary_topic="主题",
            secondary_topics=[],
            entities=[],
            conclusion="已提取核心事实。",
            bullets=["要点一", "要点二", "要点三"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        expect = EvalExpectation()
        step = evaluate_summary_step(summary, expect)
        self.assertFalse(step.passed)
        self.assertTrue(any("已提取核心事实" in item for item in step.forbidden_hits))

    def test_note_step_detects_forbidden_sections(self) -> None:
        note = """# 标题

## 这是什么
这是一个测试页面。

## 核心信息
- a
- b
- c

## 步骤细节
1. 不应出现

## 来源
- https://example.com
"""
        expect = EvalExpectation(required_keywords=["标题"])
        step = evaluate_note_step(note, expect)
        self.assertFalse(step.passed)
        self.assertTrue(any("步骤细节" in item for item in step.forbidden_hits))

    def test_note_step_requires_action_checklist_when_expected(self) -> None:
        note = """# 标题

## 这是什么
这是一个测试页面。

## 核心信息
- a
- b
- c

## 来源
- https://example.com
"""
        expect = EvalExpectation(require_action_checklist=True)
        step = evaluate_note_step(note, expect)
        self.assertFalse(step.passed)
        self.assertTrue(any("expected_procedure" in item for item in step.missing))

    def test_note_step_blocks_old_template_phrases(self) -> None:
        note = "# 标题\n\n帮助你快速理解\n\n## 核心事实\n- a\n"
        step = evaluate_note_step(note, EvalExpectation())
        self.assertFalse(step.passed)
        self.assertTrue(any("帮助你快速理解" in item or "## 核心事实" in item for item in step.forbidden_hits))

    def test_root_cause_priority(self) -> None:
        extract_step = StepScore(score=0.4, passed=False)
        signal_step = StepScore(score=1.0, passed=True)
        summary_step = StepScore(score=1.0, passed=True)
        note_step = StepScore(score=1.0, passed=True)
        cause = diagnose_root_cause(
            extract_step=extract_step,
            signal_step=signal_step,
            summary_step=summary_step,
            note_step=note_step,
            summary_mode="model",
            summary_error="",
        )
        self.assertEqual(cause, "extract")

    def test_markdown_report_contains_failure_block(self) -> None:
        report = {
            "generated_at": "2026-03-11T20:00:00",
            "case_count": 1,
            "pass_count": 0,
            "pass_rate": 0.0,
            "total_cost_usd": 0.0123,
            "results": [
                {
                    "case_id": "xhs",
                    "passed": False,
                    "overall_score": 0.63,
                    "root_cause": "summary",
                    "summary_mode": "model",
                    "cost": {"total_cost_usd": 0.0123},
                    "fix_suggestion": "升级总结模型",
                    "missing": ["keyword:tech-earnings-deepdive"],
                    "forbidden_hits": [],
                    "preview": {"file": "/tmp/a.md"},
                }
            ],
        }
        rendered = render_markdown_report(report)
        self.assertIn("## 失败明细", rendered)
        self.assertIn("keyword:tech-earnings-deepdive", rendered)
        self.assertIn("/tmp/a.md", rendered)


if __name__ == "__main__":
    unittest.main()
