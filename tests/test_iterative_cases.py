import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.iterative_cases import (
    RecognitionCase,
    append_auto_case,
    load_auto_case_inbox,
    load_recognition_cases,
    merge_recognition_cases,
    maybe_record_auto_case,
)


class IterativeCasesTest(unittest.TestCase):
    def test_load_and_merge_cases_prefers_manual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manual_path = tmp_path / "manual.json"
            manual_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "manual-1",
                            "source_kind": "url",
                            "source_url": "https://example.com",
                            "labels": ["docs"],
                            "expect": {"required_keywords": ["Example"]},
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            inbox = tmp_path / "inbox.jsonl"
            append_auto_case(
                inbox,
                source_kind="url",
                source_url="https://example.com",
                raw_text=None,
                platform_hint="docs",
                auto_reason="llm_generation_failed",
                labels=["auto"],
            )

            manual_cases = load_recognition_cases(str(manual_path))
            auto_cases = load_auto_case_inbox(str(inbox))
            merged = merge_recognition_cases(manual_cases, auto_cases)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].provenance, "manual")
        self.assertIn("docs", merged[0].labels)
        self.assertIn("auto", merged[0].labels)
        self.assertTrue(merged[0].has_expectations())

    def test_append_auto_case_dedupes_same_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "inbox.jsonl"
            append_auto_case(
                inbox,
                source_kind="url",
                source_url="https://example.com",
                raw_text=None,
                platform_hint=None,
                auto_reason="coverage_partial",
            )
            append_auto_case(
                inbox,
                source_kind="url",
                source_url="https://example.com",
                raw_text=None,
                platform_hint=None,
                auto_reason="coverage_partial",
            )
            cases = load_auto_case_inbox(str(inbox))
        self.assertEqual(len(cases), 1)

    def test_maybe_record_auto_case_uses_quality_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "inbox.jsonl"
            recorded = maybe_record_auto_case(
                inbox,
                source_kind="url",
                source_url="https://example.com",
                raw_text=None,
                platform_hint=None,
                warnings=[],
                coverage="full",
                summary_quality_score=0.6,
                dry_run=False,
                labels=["processor"],
            )
            cases = load_auto_case_inbox(str(inbox))
        self.assertTrue(recorded)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].auto_reason, "summary_quality_low")


if __name__ == "__main__":
    unittest.main()

