import json
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.analyzer.models import AnalysisOutcome, StructuredDocument

try:
    from fastapi.testclient import TestClient
    from openclaw_capture_workflow.api import create_app
    HAS_FASTAPI = True
except Exception:
    HAS_FASTAPI = False
    TestClient = None
    create_app = None


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


@unittest.skipUnless(HAS_FASTAPI, "fastapi runtime not installed in this interpreter")
class ApiTest(unittest.TestCase):
    def test_health_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(CONFIG_TEMPLATE.replace("__VAULT__", tmp), encoding="utf-8")
            client = TestClient(create_app(str(config_path)))
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_analyze_url_endpoint_returns_document_and_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(CONFIG_TEMPLATE.replace("__VAULT__", tmp), encoding="utf-8")
            app = create_app(str(config_path))
            client = TestClient(app)
            with patch(
                "openclaw_capture_workflow.api.analyze_url",
                return_value=AnalysisOutcome(
                    document=StructuredDocument(title="Example", summary="Summary", sections=[], images=[], videos=[], tables=[]),
                    warnings=["video_download_failed:https://example.com/demo.mp4:oops"],
                ),
            ):
                response = client.post("/analyze-url", json={"url": "https://example.com"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["title"], "Example")
        self.assertIn("warnings", payload)


if __name__ == "__main__":
    unittest.main()

