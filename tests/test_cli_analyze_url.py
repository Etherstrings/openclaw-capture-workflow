import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.analyzer.models import AnalysisOutcome, StructuredDocument
from openclaw_capture_workflow.cli import main


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
  "telegram": {
    "result_bot_token": "token"
  },
  "summarizer": {
    "api_base_url": "https://example.com/v1",
    "api_key": "key",
    "model": "gpt-4.1-mini",
    "timeout_seconds": 30
  }
}
"""


class CliAnalyzeUrlTest(unittest.TestCase):
    def test_analyze_url_writes_json_and_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config_path.write_text(CONFIG_TEMPLATE.replace("__VAULT__", str(tmp_path)), encoding="utf-8")
            output_path = tmp_path / "result.json"

            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch(
                "openclaw_capture_workflow.cli.analyze_url",
                return_value=AnalysisOutcome(
                    document=StructuredDocument(title="Example", summary="Summary", sections=[], images=[], videos=[], tables=[]),
                    warnings=["video_processing_failed:https://example.com/demo.mp4:download failed"],
                ),
            ), patch.object(
                sys,
                "argv",
                [
                    "openclaw-capture-workflow",
                    "analyze-url",
                    "--config",
                    str(config_path),
                    "--url",
                    "https://example.com",
                    "--output-file",
                    str(output_path),
                ],
            ), redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["title"], "Example")
            self.assertTrue(output_path.exists())
            self.assertIn("warning:", stderr.getvalue())

    def test_analyze_url_returns_non_zero_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config_path.write_text(CONFIG_TEMPLATE.replace("__VAULT__", str(tmp_path)), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch(
                "openclaw_capture_workflow.cli.analyze_url",
                side_effect=RuntimeError("boom"),
            ), patch.object(
                sys,
                "argv",
                [
                    "openclaw-capture-workflow",
                    "analyze-url",
                    "--config",
                    str(config_path),
                    "--url",
                    "https://example.com",
                ],
            ), redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertIn("error: boom", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
