import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.models import SummaryResult
from openclaw_capture_workflow.telegram import _extract_priority_project_lines, _one_line_summary


class TelegramFormatTest(unittest.TestCase):
    def test_one_line_summary_picks_first_sentence(self) -> None:
        text = "这是第一句。这里是第二句。"
        self.assertEqual(_one_line_summary(text), "这是第一句")

    def test_extract_priority_project_lines_prefers_project_and_github(self) -> None:
        summary = SummaryResult(
            title="t",
            primary_topic="p",
            secondary_topics=[],
            entities=[],
            conclusion="c",
            bullets=[
                "技能名: 美股财报深度分析 Skill",
                "项目仓库: star23/Day1Global-Skills",
                "仓库地址: https://github.com/star23/Day1Global-Skills",
            ],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        lines = _extract_priority_project_lines(summary)
        self.assertEqual(lines[0], "项目: star23/Day1Global-Skills")
        self.assertEqual(lines[1], "GitHub: https://github.com/star23/Day1Global-Skills")


if __name__ == "__main__":
    unittest.main()
