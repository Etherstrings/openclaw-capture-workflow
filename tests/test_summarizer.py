import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.models import EvidenceBundle, SummaryResult
from openclaw_capture_workflow.summarizer import _validate_and_normalize_summary


class SummarizerPostprocessTest(unittest.TestCase):
    def test_signal_facts_are_prioritized_and_generic_bullets_removed(self) -> None:
        evidence = EvidenceBundle(
            source_kind="url",
            source_url="https://www.xiaohongshu.com/explore/abc",
            platform_hint="xiaohongshu",
            title="推荐一个 Skill",
            text="推荐一个 Skill",
            evidence_type="visible_page_text",
            coverage="full",
            metadata={
                "signals": {
                    "projects": ["star23/Day1Global-Skills"],
                    "links": ["https://github.com/star23/Day1Global-Skills"],
                    "skills": ["美股财报深度分析 Skill"],
                    "skill_ids": ["tech-earnings-deepdive"],
                }
            },
        )
        summary = SummaryResult(
            title="推荐一个 Skill",
            primary_topic="技能推荐",
            secondary_topics=[],
            entities=[],
            conclusion="该证据提供了完整信息。",
            bullets=["该证据提供了完整信息。", "适用于开发者和爱好者。", "安装方法很简单。"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertTrue(any("项目名称" in item for item in normalized.bullets))
        self.assertTrue(any("GitHub地址" in item for item in normalized.bullets))
        self.assertFalse(any("该证据提供了完整信息" in item for item in normalized.bullets))
        self.assertIn("star23/Day1Global-Skills", normalized.conclusion)

    def test_title_is_cleaned_from_github_ui_prefix(self) -> None:
        evidence = EvidenceBundle(
            source_kind="url",
            source_url="https://github.com/star23/Day1Global-Skills",
            platform_hint="github",
            title="GitHub - star23/Day1Global-Skills",
            text="证据",
            evidence_type="structured_github_text",
            coverage="full",
            metadata={},
        )
        summary = SummaryResult(
            title="GitHub - star23/Day1Global-Skills",
            primary_topic="技能推荐",
            secondary_topics=[],
            entities=[],
            conclusion="识别到项目 star23/Day1Global-Skills。",
            bullets=["项目名称: star23/Day1Global-Skills", "GitHub地址: https://github.com/star23/Day1Global-Skills"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertEqual(normalized.title, "star23/Day1Global-Skills")

    def test_title_removes_bilibili_suffix(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1HpP5zBEEp",
            platform_hint="bilibili",
            title="演示视频_哔哩哔哩_bilibili",
            text="证据",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={},
        )
        summary = SummaryResult(
            title="演示视频_哔哩哔哩_bilibili",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="已提取核心事实。",
            bullets=["要点1", "要点2", "要点3"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertEqual(normalized.title, "演示视频")

    def test_tutorial_follow_up_actions_are_enriched_from_commands(self) -> None:
        evidence = EvidenceBundle(
            source_kind="url",
            source_url="https://github.com/star23/Day1Global-Skills",
            platform_hint="github",
            title="安装说明",
            text="安装方法：在 Claude 对话中使用 /install-skill 命令并上传 .skill 文件",
            evidence_type="structured_github_text",
            coverage="full",
            metadata={
                "signals": {
                    "commands": ["/install-skill https://github.com/Day1Global/Day1Global-Skills/raw/main/tech-earnings-deepdive.skill"]
                }
            },
        )
        summary = SummaryResult(
            title="安装说明",
            primary_topic="技能推荐",
            secondary_topics=[],
            entities=[],
            conclusion="按步骤安装即可。",
            bullets=["技能ID: tech-earnings-deepdive", "项目名称: star23/Day1Global-Skills", "GitHub地址: https://github.com/star23/Day1Global-Skills"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertTrue(any("/install-skill" in item for item in normalized.follow_up_actions))
        self.assertTrue(any("安装方法" in item for item in normalized.bullets))

    def test_bullets_keep_long_github_links_and_key_terms(self) -> None:
        evidence = EvidenceBundle(
            source_kind="url",
            source_url=(
                "https://github.com/kubernetes/website/blob/main/content/en/docs/"
                "setup/production-environment/container-runtimes.md"
            ),
            platform_hint="github",
            title="container-runtimes.md",
            text=(
                "Both the kubelet and the container runtime need to use the same cgroup driver.\n"
                "文档链接: https://github.com/kubernetes/website/blob/main/content/en/docs/"
                "setup/production-environment/container-runtimes.md"
            ),
            evidence_type="structured_github_text",
            coverage="full",
            metadata={
                "signals": {
                    "projects": ["kubernetes/website"],
                    "links": [
                        "https://github.com/kubernetes/website",
                        "https://github.com/kubernetes/website/blob/main/content/en/docs/setup/production-environment/container-runtimes.md",
                    ],
                }
            },
        )
        summary = SummaryResult(
            title="Kubernetes容器运行时配置",
            primary_topic="Kubernetes",
            secondary_topics=[],
            entities=[],
            conclusion="需要统一cgroup driver配置。",
            bullets=["需要统一配置。", "建议使用systemd。", "参考官方文档。"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        corpus = "\n".join(normalized.bullets)
        self.assertIn(
            "https://github.com/kubernetes/website/blob/main/content/en/docs/setup/production-environment/container-runtimes.md",
            corpus,
        )
        self.assertIn("container runtime", corpus.lower())

    def test_signal_bullets_prefer_repo_link_over_raw_skill_link(self) -> None:
        evidence = EvidenceBundle(
            source_kind="pasted_text",
            source_url=None,
            platform_hint=None,
            title="Skill推荐",
            text="项目地址: https://github.com/star23/Day1Global-Skills",
            evidence_type="raw_text",
            coverage="full",
            metadata={
                "signals": {
                    "projects": ["Day1Global/Day1Global-Skills", "star23/Day1Global-Skills"],
                    "links": [
                        "https://github.com/Day1Global/Day1Global-Skills/raw/main/tech-earnings-deepdive.skill",
                        "https://github.com/star23/Day1Global-Skills",
                        "https://github.com/Day1Global/Day1Global-Skills",
                    ],
                    "skill_ids": ["tech-earnings-deepdive"],
                }
            },
        )
        summary = SummaryResult(
            title="Skill推荐",
            primary_topic="技能推荐",
            secondary_topics=[],
            entities=[],
            conclusion="可用于美股财报分析。",
            bullets=["技能ID: tech-earnings-deepdive", "安装后可直接提问", "支持财报分析"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertTrue(normalized.bullets[0].startswith("项目名称: star23/Day1Global-Skills"))
        self.assertIn("https://github.com/star23/Day1Global-Skills", normalized.bullets[1])

    def test_non_github_video_link_is_not_labeled_as_github(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            platform_hint="youtube",
            title="Video",
            text="视频来源于YouTube",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={"signals": {"links": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]}},
        )
        summary = SummaryResult(
            title="Video",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="这是一个视频。",
            bullets=["要点1", "要点2", "要点3"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        corpus = "\n".join(normalized.bullets)
        self.assertIn("视频链接: https://www.youtube.com/watch?v=dQw4w9WgXcQ", corpus)
        self.assertNotIn("GitHub地址: https://www.youtube.com/watch?v=dQw4w9WgXcQ", corpus)

    def test_video_gate_forces_partial_coverage_and_warning_in_conclusion(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1tyNNzxEpK",
            platform_hint="bilibili",
            title="测试视频",
            text="仅提取到有限关键帧文本。",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={"video_gate_reasons": ["missing speech track (subtitle/transcript)"]},
        )
        summary = SummaryResult(
            title="测试视频",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="提取完成。",
            bullets=["要点1", "要点2", "要点3"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        self.assertEqual(normalized.coverage, "partial")
        self.assertEqual(normalized.confidence, "medium")
        self.assertIn("证据不完整", normalized.conclusion)

    def test_signal_links_strip_tracking_params_in_bullets(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7?xsec_token=abc&xsec_source=pc_feed",
            platform_hint="xiaohongshu",
            title="小红书视频",
            text="正文",
            evidence_type="multimodal_video",
            coverage="full",
            metadata={
                "signals": {
                    "links": [
                        "https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7?xsec_token=abc&xsec_source=pc_feed"
                    ]
                }
            },
        )
        summary = SummaryResult(
            title="小红书视频",
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion="完成。",
            bullets=["要点1", "要点2", "要点3"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        normalized = _validate_and_normalize_summary(summary, evidence)
        joined = "\n".join(normalized.bullets)
        self.assertIn("https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7", joined)
        self.assertNotIn("xsec_token=", joined)


if __name__ == "__main__":
    unittest.main()
