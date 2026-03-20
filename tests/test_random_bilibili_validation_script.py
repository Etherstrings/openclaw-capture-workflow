import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_random_bilibili_validation.py"
    spec = importlib.util.spec_from_file_location("random_bili_validation_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load run_random_bilibili_validation.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


random_validation = _load_module()


class RandomBilibiliValidationScriptTest(unittest.TestCase):
    def test_choose_urls_falls_back_when_search_fails(self) -> None:
        spec = random_validation.CategorySpec(
            category_id="review",
            label="测评/横评",
            keywords=["手机 评测"],
            fallback_urls=["https://www.bilibili.com/video/BV1fallback/"],
        )
        with patch.object(random_validation, "_fetch_bilibili_search", side_effect=RuntimeError("blocked")):
            urls = random_validation._choose_urls(spec, random_validation.random.Random(1), 1)
        self.assertEqual(urls, [("fallback", "https://www.bilibili.com/video/BV1fallback/")])

    def test_score_case_penalizes_generic_topic_statement(self) -> None:
        payload = {
            "status": "done",
            "result": {
                "outcome": "partial",
                "summary": {
                    "conclusion": "已提取核心事实，可先留档。",
                    "bullets": ["要点A", "要点B"],
                    "coverage": "partial",
                },
                "quality_gate": {"outcome": "partial"},
            },
        }
        score = random_validation._score_case(payload)
        self.assertLessEqual(score.topic_clarity, 1)
        self.assertTrue(any("主题句不够直接" in item for item in score.notes))

    def test_score_case_penalizes_low_outline_retention(self) -> None:
        payload = {
            "status": "done",
            "result": {
                "outcome": "partial",
                "summary": {
                    "conclusion": "视频主要讲面试常见问题与回答方法。",
                    "bullets": ["问题一", "问题二", "问题三"],
                    "timeline_sections": [{"heading": "片段1"}, {"heading": "片段2"}],
                    "coverage": "partial",
                    "uncertainties": ["当前覆盖不足。"],
                },
                "quality_gate": {
                    "outcome": "partial",
                    "mode": "probe",
                    "probe_quality": "strong",
                    "expected_outline_count": 12,
                    "retained_outline_count": 4,
                },
            },
        }
        score = random_validation._score_case(payload)
        self.assertLess(score.total, 10)
        self.assertTrue(any("主线覆盖偏低" in item for item in score.notes))


if __name__ == "__main__":
    unittest.main()
