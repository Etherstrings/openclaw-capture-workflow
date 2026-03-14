import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.analyzer.render import PinchTabBackend


class AnalyzerRenderTest(unittest.TestCase):
    def test_pinchtab_backend_renders_text_and_screenshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            backend = PinchTabBackend(base_url="http://127.0.0.1:9867")

            def fake_json(method, path, payload=None):
                if path == "/health":
                    return {"status": "ok"}
                if path == "/navigate":
                    return {"tabId": "tab-1", "title": "Example", "url": "https://example.com"}
                if path == "/tabs/tab-1/text?raw=true":
                    return {"title": "Example", "url": "https://example.com", "text": "Line one\nLine two"}
                raise AssertionError(path)

            with patch.object(backend, "_http_json", side_effect=fake_json), patch.object(
                backend,
                "_http_bytes",
                return_value=b"png-bytes",
            ):
                rendered = backend.render("https://example.com", tmp_path, timeout_seconds=30)

        self.assertEqual(rendered.title, "Example")
        self.assertIn("Line one", rendered.text_hint)
        self.assertTrue(rendered.screenshot_path)
        self.assertEqual(rendered.metadata["backend"], "pinchtab")


if __name__ == "__main__":
    unittest.main()

