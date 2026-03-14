import json
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.analyzer.models import AnalysisOutcome, SectionResult, StructuredDocument
from openclaw_capture_workflow.search_fallback import SearchEvidenceBundle, SearchResultItem, FetchedPage
from openclaw_capture_workflow.iterative_runner import run_iterative_recognition


CONFIG_TEMPLATE = """
{
  "listen_host": "127.0.0.1",
  "listen_port": 8765,
  "state_dir": "./state",
  "obsidian": {
    "vault_path": "__VAULT__",
    "inbox_root": "Inbox/OpenClaw",
    "topics_root": "Topics",
    "entities_root": "Entities",
    "auto_topic_whitelist": [],
    "auto_topic_blocklist": []
  },
  "telegram": {"result_bot_token": "token"},
  "summarizer": {
    "api_base_url": "https://example.com/v1",
    "api_key": "key",
    "model": "gpt-4.1-mini",
    "timeout_seconds": 30
  }
}
"""


class _FakeLlm:
    def __init__(self, config) -> None:
        self.config = config

    def generate_document(self, extracted, requested_output_lang, screenshot_path):
        return StructuredDocument(
            title="Searched Title",
            summary="Searched summary with extra context.",
            sections=[SectionResult(heading="Search Enrichment", level=2, content=extracted.main_text[:200])],
            images=[],
            videos=[],
            tables=[],
        )


class IterativeRunnerTest(unittest.TestCase):
    def test_run_iterative_recognition_generates_previews_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config_path.write_text(CONFIG_TEMPLATE.replace("__VAULT__", str(tmp_path)), encoding="utf-8")
            cases_path = tmp_path / "cases.json"
            cases_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "manual-case",
                            "source_kind": "url",
                            "source_url": "https://example.com",
                            "labels": ["docs"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            baseline_doc = StructuredDocument(
                title="Baseline Title",
                summary="Baseline summary",
                sections=[SectionResult(heading="Overview", level=1, content="Baseline body")],
                images=[],
                videos=[],
                tables=[],
            )
            search_bundle = SearchEvidenceBundle(
                queries=["site:example.com baseline"],
                results=[SearchResultItem(title="Search hit", url="https://example.com/help", snippet="extra snippet")],
                fetched_pages=[FetchedPage(url="https://example.com/help", title="Help", text="Extra context from search")],
                evidence_text="[搜索补充页面]\n标题: Help\n内容: Extra context from search",
                warnings=[],
            )
            with patch(
                "openclaw_capture_workflow.iterative_runner.analyze_url",
                return_value=AnalysisOutcome(document=baseline_doc, warnings=["llm_generation_failed:test"]),
            ), patch(
                "openclaw_capture_workflow.iterative_runner.run_search_enrichment",
                return_value=search_bundle,
            ), patch(
                "openclaw_capture_workflow.iterative_runner.OpenAIResponsesClient",
                _FakeLlm,
            ):
                report = run_iterative_recognition(
                    config_path=str(config_path),
                    cases_path=str(cases_path),
                    case_source="manual",
                )

            self.assertEqual(report["case_count"], 1)
            self.assertTrue(Path(report["report_markdown"]).exists())
            self.assertTrue(Path(report["report_json"]).exists())
            result = report["results"][0]
            self.assertIn("baseline", result["preview_files"])
            self.assertTrue(Path(result["preview_files"]["baseline"]).exists())
            self.assertEqual(result["chosen"]["label"], "searched")


if __name__ == "__main__":
    unittest.main()

