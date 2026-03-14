import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.analyzer.models import AnalysisOutcome, RenderResult, StructuredDocument
from openclaw_capture_workflow.analyzer.service import analyze_url
from openclaw_capture_workflow.config import AnalysisConfig, AppConfig, ExtractorConfig, ObsidianConfig, SummarizerConfig, TelegramConfig
from openclaw_capture_workflow.iterative_cases import load_auto_case_inbox


class _FakeBackend:
    def render(self, url, temp_dir, timeout_seconds):
        screenshot = temp_dir / "page.png"
        screenshot.write_bytes(b"png")
        return RenderResult(
            requested_url=url,
            final_url=url,
            title="Example",
            html="<html><body><main><h1>Example</h1><p>Hello world</p></main></body></html>",
            screenshot_path=screenshot,
        )


class _FailingBackend:
    def render(self, url, temp_dir, timeout_seconds):
        raise RuntimeError("render failed")


class _FakeLlm:
    def generate_document(self, extracted, requested_output_lang, screenshot_path):
        return StructuredDocument(title="Example", summary="Summary", sections=[], images=[], videos=[], tables=[])


class _FailingLlm:
    def generate_document(self, extracted, requested_output_lang, screenshot_path):
        raise RuntimeError("llm boom")


class _ShortTextBackend:
    def render(self, url, temp_dir, timeout_seconds):
        screenshot = temp_dir / "page.png"
        screenshot.write_bytes(b"png")
        return RenderResult(
            requested_url=url,
            final_url=url,
            title="Example",
            html="<html><body><main><p>short</p></main></body></html>",
            screenshot_path=screenshot,
            text_hint="short",
        )


class _PinchTabBackend:
    def render(self, url, temp_dir, timeout_seconds):
        screenshot = temp_dir / "pinch.png"
        screenshot.write_bytes(b"png")
        return RenderResult(
            requested_url=url,
            final_url=url,
            title="PinchTab Example",
            html="<html><body><main><h1>PinchTab Example</h1><p>Recovered with PinchTab backend and much richer content.</p></main></body></html>",
            screenshot_path=screenshot,
            text_hint="Recovered with PinchTab backend and much richer content.",
        )


def _config(tmp: str) -> AppConfig:
    return AppConfig(
        listen_host="127.0.0.1",
        listen_port=8765,
        state_dir="state",
        obsidian=ObsidianConfig(
            vault_path=tmp,
            inbox_root="Inbox/OpenClaw",
            topics_root="Topics",
            entities_root="Entities",
            auto_topic_whitelist=[],
            auto_topic_blocklist=[],
        ),
        telegram=TelegramConfig(result_bot_token="token"),
        summarizer=SummarizerConfig(api_base_url="https://example.com/v1", api_key="key", model="unused", timeout_seconds=30),
        extractors=ExtractorConfig(),
        analysis=AnalysisConfig(temp_root="tmp"),
    )


class AnalyzerCleanupTest(unittest.TestCase):
    def test_analyze_url_cleans_temp_dir_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(tmp)
            state_dir = Path(tmp) / "state"
            state_dir.mkdir()
            outcome = analyze_url(
                url="https://example.com",
                requested_output_lang="zh-CN",
                config=cfg,
                state_dir=state_dir,
                backend=_FakeBackend(),
                llm_client=_FakeLlm(),
            )

            self.assertIsInstance(outcome, AnalysisOutcome)
            temp_root = state_dir / "tmp"
            self.assertTrue(temp_root.exists())
            self.assertEqual(list(temp_root.iterdir()), [])

    def test_analyze_url_cleans_temp_dir_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(tmp)
            state_dir = Path(tmp) / "state"
            state_dir.mkdir()
            with self.assertRaises(RuntimeError):
                analyze_url(
                    url="https://example.com",
                    requested_output_lang="zh-CN",
                    config=cfg,
                    state_dir=state_dir,
                    backend=_FailingBackend(),
                    llm_client=_FakeLlm(),
                )
            temp_root = state_dir / "tmp"
            self.assertTrue(temp_root.exists())
            self.assertEqual(list(temp_root.iterdir()), [])

    def test_analyze_url_falls_back_to_pinchtab_on_short_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(tmp)
            cfg.analysis.pinchtab_base_url = "http://127.0.0.1:9867"
            state_dir = Path(tmp) / "state"
            state_dir.mkdir()
            with patch("openclaw_capture_workflow.analyzer.service.PinchTabBackend", return_value=_PinchTabBackend()):
                outcome = analyze_url(
                    url="https://example.com",
                    requested_output_lang="zh-CN",
                    config=cfg,
                    state_dir=state_dir,
                    backend=_ShortTextBackend(),
                    llm_client=_FakeLlm(),
                )

            self.assertIsInstance(outcome, AnalysisOutcome)
            self.assertIn("playwright_text_short_fallback_to_pinchtab", outcome.warnings)

    def test_analyze_url_records_auto_case_when_warnings_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(tmp)
            state_dir = Path(tmp) / "state"
            state_dir.mkdir()
            inbox = state_dir / "cases" / "inbox.jsonl"
            outcome = analyze_url(
                url="https://example.com",
                requested_output_lang="zh-CN",
                config=cfg,
                state_dir=state_dir,
                backend=_FakeBackend(),
                llm_client=_FailingLlm(),
                auto_case_sink=inbox,
                auto_case_source_kind="url",
                auto_case_platform_hint="docs",
            )
            cases = load_auto_case_inbox(str(inbox))

        self.assertIsInstance(outcome, AnalysisOutcome)
        self.assertTrue(cases)
        self.assertEqual(cases[0].provenance, "auto")


if __name__ == "__main__":
    unittest.main()
