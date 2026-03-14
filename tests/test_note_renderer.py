import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.models import EvidenceBundle, SummaryResult
from openclaw_capture_workflow.note_renderer import (
    build_note_materials,
    build_note_user_prompt,
    load_note_system_prompt,
    save_materials_file,
)


class NoteRendererTest(unittest.TestCase):
    def test_load_note_system_prompt_prefers_human_brief_style(self) -> None:
        prompt = load_note_system_prompt()
        self.assertIn("只根据给定物料总结", prompt)
        self.assertIn("不要编造", prompt)
        self.assertIn("直接返回 Markdown 正文", prompt)
        self.assertIn("替用户写一份已经看完内容后的简报", prompt)
        self.assertIn("有人味", prompt)
        self.assertIn("贾维斯的思考", prompt)
        self.assertNotIn("可直接做的下一步", prompt)
        self.assertNotIn("复习", prompt)

    def test_build_note_materials_contains_raw_inputs(self) -> None:
        materials = build_note_materials(
            summary=SummaryResult(
                title="Example Domain",
                primary_topic="Example Domain",
                secondary_topics=[],
                entities=[],
                conclusion="这是一个示例域名页面。",
                bullets=["用于文档示例", "不要在生产环境使用"],
                evidence_quotes=["This domain is for use in documentation examples without needing permission."],
                coverage="full",
                confidence="high",
                note_tags=[],
                follow_up_actions=[],
            ),
            evidence=EvidenceBundle(
                source_kind="url",
                source_url="https://example.com/",
                platform_hint="web",
                title="Example Domain",
                text="Example Domain\nThis domain is for use in documentation examples without needing permission.",
                evidence_type="visible_page_text",
                coverage="full",
                metadata={"structured_document": {"title": "Example Domain", "summary": "示例页面。"}},
            ),
            structure_map="unused",
            topic_links=[],
            entity_links=[],
            keyword_links=[],
            skipped_topics=[],
            canonical_source_url="https://example.com/",
        )
        self.assertEqual(materials["title"], "Example Domain")
        self.assertIn("structured_document", materials)
        self.assertIn("summary", materials)
        self.assertIn("fragments", materials)
        self.assertIn("warnings", materials)

    def test_build_note_materials_contains_video_story_context(self) -> None:
        materials = build_note_materials(
            summary=SummaryResult(
                title="OpenClaw股票量化交易推荐",
                primary_topic="OpenClaw量化交易",
                secondary_topics=[],
                entities=[],
                conclusion="视频展示了如何使用OpenClaw进行股票量化分析并生成每日交易建议。",
                bullets=["主线", "流程", "风险"],
                evidence_quotes=[],
                coverage="full",
                confidence="high",
                note_tags=[],
                follow_up_actions=[],
            ),
            evidence=EvidenceBundle(
                source_kind="video_url",
                source_url="https://www.bilibili.com/video/BV1bFPMzFEnd",
                platform_hint="bilibili",
                title="OpenClaw你虾哥每天股票量化交易推荐",
                text="正文",
                evidence_type="multimodal_video",
                coverage="full",
                metadata={
                    "video_story_blocks": [
                        {"label": "core_topic", "summary": "视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议。", "evidence": []}
                    ],
                    "viewer_feedback": ["评论区有人觉得这套方法有参考价值"],
                    "viewer_feedback_capture": {"attempted": True, "count": 1, "warning": None},
                    "user_guidance": "重点看这个视频值不值得继续学。",
                },
            ),
            structure_map="unused",
            topic_links=[],
            entity_links=[],
            keyword_links=[],
            skipped_topics=[],
            canonical_source_url="https://www.bilibili.com/video/BV1bFPMzFEnd",
        )
        self.assertIn("video_story_blocks", materials["fragments"])
        self.assertIn("viewer_feedback", materials["fragments"])
        self.assertEqual(materials["context"]["user_guidance"], "重点看这个视频值不值得继续学。")

    def test_build_note_materials_sanitizes_warning_summary_for_video_block(self) -> None:
        materials = build_note_materials(
            summary=SummaryResult(
                title="小红书页面丢失",
                primary_topic="视频",
                secondary_topics=[],
                entities=[],
                conclusion="当前拿不到有效内容。",
                bullets=[],
                evidence_quotes=[],
                coverage="partial",
                confidence="low",
                note_tags=[],
                follow_up_actions=[],
            ),
            evidence=EvidenceBundle(
                source_kind="video_url",
                source_url="https://www.xiaohongshu.com/explore/69aea021000000001a028a59",
                platform_hint="xiaohongshu",
                title="小红书 - 你访问的页面不见了",
                text="正文",
                evidence_type="multimodal_video",
                coverage="partial",
                metadata={
                    "fetch_warnings": [
                        "video_audio_failed: Unsupported URL ... Python 3.9 ...",
                        "video_keyframes_failed: No video formats found! ... yt-dlp ...",
                    ],
                    "tracks": {
                        "has_subtitle": False,
                        "has_transcript": False,
                        "has_keyframes": False,
                        "has_keyframe_ocr": False,
                    },
                },
            ),
            structure_map="unused",
            topic_links=[],
            entity_links=[],
            keyword_links=[],
            skipped_topics=[],
            canonical_source_url="https://www.xiaohongshu.com/explore/69aea021000000001a028a59",
        )
        self.assertIn("未能拿到音频轨。", materials["warnings"])
        self.assertIn("未能拿到视频关键帧。", materials["warnings"])
        self.assertEqual(materials["context"]["capture_status"]["kind"], "video_extract_blocked")

    def test_user_prompt_only_wraps_materials_json(self) -> None:
        prompt = build_note_user_prompt({"title": "Example Domain", "summary": {"conclusion": "测试"}})
        self.assertIn("下面是物料 JSON", prompt)
        self.assertIn("Example Domain", prompt)
        self.assertNotIn("学习笔记", prompt)
        self.assertNotIn("复习", prompt)
        self.assertNotIn("## ", prompt)

    def test_save_materials_file_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = save_materials_file({"title": "Example Domain"}, Path(tmp), "Example Domain")
            self.assertTrue(Path(path).exists())
            self.assertIn("Example Domain", Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
