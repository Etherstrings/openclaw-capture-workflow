import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.analyzer.llm import OpenAIResponsesClient
from openclaw_capture_workflow.analyzer.models import ExtractedContent, SectionResult, StructuredDocument
from openclaw_capture_workflow.config import AnalysisConfig, AppConfig, ExtractorConfig, ObsidianConfig, SummarizerConfig, TelegramConfig


class _StubResponsesClient(OpenAIResponsesClient):
    def __init__(self, config: AppConfig, responses):
        super().__init__(config)
        self.responses = list(responses)
        self.models = []

    def _perform_request(self, model, extracted, requested_output_lang, screenshot_path):
        self.models.append(model)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


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
        analysis=AnalysisConfig(model="gpt-5-mini", fallback_model="gpt-5.4"),
    )


class AnalyzerLlmTest(unittest.TestCase):
    def test_generate_document_uses_primary_model_when_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _StubResponsesClient(
                _config(tmp),
                [json.dumps({"title": "T", "summary": "A usable summary", "sections": [{"heading": "Intro", "level": 1, "content": "Body details"}], "images": [], "videos": [], "tables": []})],
            )
            doc = client.generate_document(
                extracted=ExtractedContent(title="T", main_text="Body", sections=[SectionResult(heading="Intro", level=1, content="Body")]),
                requested_output_lang="zh-CN",
                screenshot_path=None,
            )
        self.assertIsInstance(doc, StructuredDocument)
        self.assertEqual(client.models, ["gpt-5-mini"])

    def test_generate_document_retries_with_fallback_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _StubResponsesClient(
                _config(tmp),
                [
                    RuntimeError("primary failed"),
                    json.dumps({"title": "T", "summary": "A usable summary", "sections": [{"heading": "Intro", "level": 1, "content": "Body details"}], "images": [], "videos": [], "tables": []}),
                ],
            )
            doc = client.generate_document(
                extracted=ExtractedContent(title="T", main_text="Body", sections=[SectionResult(heading="Intro", level=1, content="Body")]),
                requested_output_lang="zh-CN",
                screenshot_path=None,
            )
        self.assertEqual(doc.title, "T")
        self.assertEqual(client.models, ["gpt-5-mini", "gpt-5.4"])

    def test_generate_document_retries_when_primary_quality_is_bad(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _StubResponsesClient(
                _config(tmp),
                [
                    json.dumps({"title": "T", "summary": "T", "sections": [{"heading": "Intro", "level": 1, "content": "Body"}], "images": [], "videos": [], "tables": []}),
                    json.dumps({"title": "T", "summary": "A usable summary", "sections": [{"heading": "Intro", "level": 1, "content": "A longer body section for quality checks."}], "images": [], "videos": [], "tables": []}),
                ],
            )
            doc = client.generate_document(
                extracted=ExtractedContent(title="T", main_text="Body", sections=[SectionResult(heading="Intro", level=1, content="Body")]),
                requested_output_lang="zh-CN",
                screenshot_path=None,
            )
        self.assertEqual(doc.summary, "A usable summary")
        self.assertEqual(client.models, ["gpt-5-mini", "gpt-5.4"])


if __name__ == "__main__":
    unittest.main()
