import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.analyzer.models import (
    ImageResult,
    SectionResult,
    StructuredDocument,
    TableResult,
    VideoResult,
)


class StructuredDocumentModelTest(unittest.TestCase):
    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        document = StructuredDocument(
            title="Example",
            summary="A short summary.",
            sections=[SectionResult(heading="Intro", level=1, content="Paragraph")],
            images=[ImageResult(src="https://example.com/a.png", alt="diagram", caption="caption", context="context")],
            videos=[VideoResult(src="https://example.com/a.mp4", poster="https://example.com/a.jpg", provider="example", duration_seconds=3.2, frame_summaries=["frame 1"])],
            tables=[TableResult(caption="stats", headers=["A"], rows=[["1"]])],
        )

        payload = document.to_dict()
        restored = StructuredDocument.from_dict(payload)

        self.assertEqual(restored.to_dict(), payload)

    def test_from_dict_requires_title_and_summary(self) -> None:
        with self.assertRaises(ValueError):
            StructuredDocument.from_dict({"title": "", "summary": "", "sections": [], "images": [], "videos": [], "tables": []})


if __name__ == "__main__":
    unittest.main()

