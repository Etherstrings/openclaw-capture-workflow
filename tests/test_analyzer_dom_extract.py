import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.analyzer.dom_extract import extract_content
from openclaw_capture_workflow.analyzer.models import RenderResult


HTML = """
<html>
  <head><title>Example Product</title></head>
  <body>
    <nav>Navigation</nav>
    <main>
      <h1>Example Product</h1>
      <p>This is the first paragraph.</p>
      <h2>Usage</h2>
      <p>Run the tool with a URL.</p>
      <img src="/hero.png" alt="hero image" />
      <video poster="/poster.jpg">
        <source src="/demo.mp4" />
      </video>
      <table>
        <caption>Metrics</caption>
        <tr><th>Name</th><th>Value</th></tr>
        <tr><td>Latency</td><td>Fast</td></tr>
      </table>
    </main>
    <footer>Footer</footer>
  </body>
</html>
"""


class AnalyzerDomExtractTest(unittest.TestCase):
    def test_extract_content_from_rendered_html(self) -> None:
        render = RenderResult(
            requested_url="https://example.com/demo",
            final_url="https://example.com/demo",
            title="Example Product",
            html=HTML,
            text_hint="",
        )

        extracted = extract_content(render, max_images=4, max_videos=2, max_tables=2)

        self.assertEqual(extracted.title, "Example Product")
        self.assertIn("This is the first paragraph.", extracted.main_text)
        self.assertGreaterEqual(len(extracted.sections), 1)
        self.assertEqual(extracted.images[0].result.src, "https://example.com/hero.png")
        self.assertEqual(extracted.images[0].result.alt, "hero image")
        self.assertEqual(extracted.videos[0].result.src, "https://example.com/demo.mp4")
        self.assertEqual(extracted.videos[0].result.poster, "https://example.com/poster.jpg")
        self.assertEqual(extracted.tables[0].caption, "Metrics")
        self.assertEqual(extracted.tables[0].headers, ["Name", "Value"])

    def test_falls_back_to_text_hint_when_body_empty(self) -> None:
        render = RenderResult(
            requested_url="https://example.com/empty",
            final_url="https://example.com/empty",
            title="Empty",
            html="<html><body><nav>Only navigation</nav></body></html>",
            text_hint="Fallback body text",
        )

        extracted = extract_content(render, max_images=2, max_videos=1, max_tables=1)

        self.assertEqual(extracted.main_text, "Fallback body text")
        self.assertEqual(extracted.sections[0].content, "Fallback body text")


if __name__ == "__main__":
    unittest.main()

