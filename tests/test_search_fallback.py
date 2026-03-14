import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.analyzer.models import SectionResult, StructuredDocument
from openclaw_capture_workflow.iterative_cases import RecognitionCase
from openclaw_capture_workflow.search_fallback import (
    FetchedPage,
    SearchEvidenceBundle,
    SearchResultItem,
    build_broad_query,
    build_site_query,
    extract_search_results_from_snapshot,
    run_search_enrichment,
)


class _FakeBrowserClient:
    def __init__(self) -> None:
        self.opened = []

    def open_url(self, url: str) -> str:
        self.opened.append(url)
        if "duckduckgo.com" in url:
            return "search-tab"
        return "page-tab"

    def evaluate(self, target_id: str, fn_source: str):
        if target_id == "search-tab":
            return [
                {
                    "title": "OpenAI Cookbook",
                    "url": "https://github.com/openai/openai-cookbook",
                    "snippet": "Examples and guides for using the OpenAI API",
                }
            ]
        return {
            "title": "OpenAI Cookbook",
            "text": "Examples and guides for using the OpenAI API with notebooks and setup instructions.",
        }

    def snapshot(self, target_id: str, limit: int = 250) -> str:
        return ""

    def close(self, target_id: str) -> None:
        return


class SearchFallbackTest(unittest.TestCase):
    def test_build_queries(self) -> None:
        case = RecognitionCase(
            case_id="case-1",
            source_kind="url",
            source_url="https://github.com/openai/openai-cookbook",
            raw_text=None,
            platform_hint="github",
            labels=["github", "openai"],
        )
        document = StructuredDocument(
            title="GitHub - openai/openai-cookbook",
            summary="Examples and guides for using the OpenAI API",
            sections=[SectionResult(heading="Overview", level=1, content="Examples and guides.")],
        )
        site_query = build_site_query(case, document)
        broad_query = build_broad_query(case, document)

        self.assertIn("site:github.com", site_query)
        self.assertIn("openai", broad_query.lower())

    def test_extract_search_results_from_snapshot(self) -> None:
        snapshot = """
        - link "OpenAI Cookbook"
        - /url: https://github.com/openai/openai-cookbook
        - text: Examples and guides for using the OpenAI API
        - generic [ref=e1]: notebooks and setup
        - link "Python Tutorial"
        - /url: https://docs.python.org/3/tutorial/index.html
        - text: Learn Python basics
        """
        items = extract_search_results_from_snapshot(snapshot, limit=5)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "OpenAI Cookbook")
        self.assertIn("OpenAI API", items[0].snippet)

    def test_run_search_enrichment_collects_results_and_pages(self) -> None:
        case = RecognitionCase(
            case_id="case-2",
            source_kind="url",
            source_url="https://github.com/openai/openai-cookbook",
            raw_text=None,
            platform_hint="github",
            labels=["github"],
        )
        document = StructuredDocument(
            title="GitHub - openai/openai-cookbook",
            summary="Examples and guides for using the OpenAI API",
            sections=[SectionResult(heading="Overview", level=1, content="Examples and guides.")],
        )
        bundle = run_search_enrichment(
            case,
            document,
            client=_FakeBrowserClient(),
            max_results=5,
            max_pages=2,
        )
        self.assertIsInstance(bundle, SearchEvidenceBundle)
        self.assertEqual(len(bundle.results), 1)
        self.assertEqual(len(bundle.fetched_pages), 1)
        self.assertIn("OpenAI API", bundle.evidence_text)


if __name__ == "__main__":
    unittest.main()

