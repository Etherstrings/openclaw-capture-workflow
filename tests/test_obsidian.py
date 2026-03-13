import tempfile
import unittest
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.config import ObsidianConfig
from openclaw_capture_workflow.models import EvidenceBundle, SummaryResult
from openclaw_capture_workflow.obsidian import ObsidianWriter


class ObsidianWriterTest(unittest.TestCase):
    def test_write_creates_main_note_and_topic_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "股票"],
                    auto_topic_blocklist=["测试", "总结", "路径"],
                    auto_entity_pages=False,
                )
            )
            summary = SummaryResult(
                title="AI 和股票联动",
                primary_topic="AI",
                secondary_topics=["股票"],
                entities=["OpenAI", "英伟达"],
                conclusion="内容同时涉及 AI 应用和股票影响。",
                bullets=["AI 产品更新", "资本市场反应", "相关产业链"],
                evidence_quotes=["AI 产品更新", "资本市场反应"],
                coverage="full",
                confidence="high",
                note_tags=["AI", "股票"],
                follow_up_actions=[],
            )
            evidence = EvidenceBundle(
                source_kind="url",
                source_url="https://example.com",
                platform_hint="web",
                title="原始标题",
                text="正文",
                evidence_type="visible_page_text",
                coverage="full",
            )
            note = writer.write(summary, evidence)
            self.assertTrue((writer.vault_path / note["note_path"]).exists())
            self.assertTrue(str(note["obsidian_uri"]).startswith("obsidian://open?vault="))
            self.assertTrue((writer.vault_path / "Topics/AI/AI Index.md").exists())
            self.assertTrue((writer.vault_path / "Topics/股票/股票 Index.md").exists())
            self.assertFalse((writer.vault_path / "Entities/OpenAI.md").exists())

    def test_non_whitelist_topics_do_not_auto_create_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "股票"],
                    auto_topic_blocklist=["测试", "总结", "路径"],
                    auto_entity_pages=False,
                )
            )
            summary = SummaryResult(
                title="心理观察",
                primary_topic="心理",
                secondary_topics=["工作", "测试路径"],
                entities=[],
                conclusion="围绕心理和工作展开。",
                bullets=["心理", "工作"],
                evidence_quotes=["心理", "工作"],
                coverage="full",
                confidence="high",
                note_tags=[],
                follow_up_actions=[],
            )
            evidence = EvidenceBundle(
                source_kind="pasted_text",
                source_url=None,
                platform_hint="local",
                title=None,
                text="正文",
                evidence_type="raw_text",
                coverage="full",
            )
            note = writer.write(summary, evidence)
            self.assertFalse((writer.vault_path / "Topics/心理/心理 Index.md").exists())
            self.assertFalse((writer.vault_path / "Topics/工作/工作 Index.md").exists())
            self.assertFalse((writer.vault_path / "Topics/测试路径/测试路径 Index.md").exists())
            self.assertEqual(note["topic_links"], [])

    def test_established_non_whitelist_primary_topic_can_be_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "股票"],
                    auto_topic_blocklist=["测试", "总结", "路径"],
                    auto_entity_pages=False,
                )
            )
            established_index = writer.vault_path / "Topics/心理/心理 Index.md"
            established_index.parent.mkdir(parents=True, exist_ok=True)
            established_index.write_text(
                "# 心理\n\n## 笔记\n- [[Inbox/OpenClaw/2026/03/a.md]]\n- [[Inbox/OpenClaw/2026/03/b.md]]\n",
                encoding="utf-8",
            )
            note = writer.write(
                SummaryResult(
                    title="心理观察",
                    primary_topic="心理",
                    secondary_topics=[],
                    entities=[],
                    conclusion="围绕心理展开。",
                    bullets=["心理"],
                    evidence_quotes=["心理"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[],
                ),
                EvidenceBundle(
                    source_kind="pasted_text",
                    source_url=None,
                    platform_hint="local",
                    title=None,
                    text="正文",
                    evidence_type="raw_text",
                    coverage="full",
                ),
            )
            self.assertIn("[[Topics/心理/心理 Index.md]]", note["topic_links"])

    def test_same_source_url_reuses_existing_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "股票"],
                    auto_topic_blocklist=["测试", "总结", "路径"],
                    auto_entity_pages=False,
                )
            )
            evidence = EvidenceBundle(
                source_kind="url",
                source_url="https://github.com/VoltAgent/awesome-openclaw-skills",
                platform_hint="github",
                title="原始标题",
                text="正文",
                evidence_type="structured_github_text",
                coverage="full",
            )
            first = writer.write(
                SummaryResult(
                    title="第一次标题",
                    primary_topic="GitHub",
                    secondary_topics=[],
                    entities=[],
                    conclusion="第一次总结。",
                    bullets=["第一次"],
                    evidence_quotes=["第一次"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[],
                ),
                evidence,
            )
            second = writer.write(
                SummaryResult(
                    title="第二次标题",
                    primary_topic="GitHub",
                    secondary_topics=[],
                    entities=[],
                    conclusion="第二次总结。",
                    bullets=["第二次"],
                    evidence_quotes=["第二次"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[],
                ),
                evidence,
            )
            self.assertEqual(first["note_path"], second["note_path"])
            note_path = writer.vault_path / str(second["note_path"])
            content = note_path.read_text(encoding="utf-8")
            self.assertIn("title: 第二次标题", content)
            self.assertIn("第二次总结。", content)

    def test_same_source_url_prefers_latest_existing_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "股票"],
                    auto_topic_blocklist=["测试", "总结", "路径"],
                    auto_entity_pages=False,
                )
            )
            older_rel = Path("Inbox/OpenClaw/2026/03/2026-03-10 0140 Awesome OpenClaw Skills.md")
            newer_rel = Path("Inbox/OpenClaw/2026/03/2026-03-10 0209 Awesome OpenClaw Skills.md")
            for rel, title in [(older_rel, "旧笔记"), (newer_rel, "新笔记")]:
                path = writer.vault_path / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "\n".join(
                        [
                            "---",
                            "title: " + title,
                            "source_url: https://github.com/VoltAgent/awesome-openclaw-skills",
                            "source_kind: url",
                            "platform: github",
                            "coverage: full",
                            "confidence: high",
                            "topics: OpenClaw",
                            "entities: ",
                            "---",
                            "",
                            "# " + title,
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )

            note = writer.write(
                SummaryResult(
                    title="更新后的笔记",
                    primary_topic="GitHub",
                    secondary_topics=[],
                    entities=[],
                    conclusion="更新后的总结。",
                    bullets=["更新"],
                    evidence_quotes=["更新"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[],
                ),
                EvidenceBundle(
                    source_kind="url",
                    source_url="https://github.com/VoltAgent/awesome-openclaw-skills",
                    platform_hint="github",
                    title="原始标题",
                    text="正文",
                    evidence_type="structured_github_text",
                    coverage="full",
                ),
            )
            self.assertEqual(note["note_path"], newer_rel.as_posix())
            newer_content = (writer.vault_path / newer_rel).read_text(encoding="utf-8")
            self.assertIn("title: 更新后的笔记", newer_content)

    def test_rewrite_removes_stale_topic_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "股票", "OpenClaw"],
                    auto_topic_blocklist=["测试", "总结", "路径"],
                    auto_entity_pages=False,
                )
            )
            evidence = EvidenceBundle(
                source_kind="url",
                source_url="https://example.com/openclaw",
                platform_hint="web",
                title="原始标题",
                text="正文足够长，包含 OpenClaw 安装和飞书接入说明。",
                evidence_type="visible_page_text",
                coverage="full",
            )
            writer.write(
                SummaryResult(
                    title="第一次标题",
                    primary_topic="云计算",
                    secondary_topics=[],
                    entities=[],
                    conclusion="第一次总结。",
                    bullets=["第一次"],
                    evidence_quotes=["第一次"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[],
                ),
                evidence,
            )
            writer.write(
                SummaryResult(
                    title="第二次标题",
                    primary_topic="OpenClaw",
                    secondary_topics=["AI"],
                    entities=[],
                    conclusion="第二次总结。",
                    bullets=["第二次"],
                    evidence_quotes=["第二次"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[],
                ),
                evidence,
            )
            cloud_index = writer.vault_path / "Topics/云计算/云计算 Index.md"
            self.assertFalse(cloud_index.exists() and "[[Inbox/OpenClaw/" in cloud_index.read_text(encoding="utf-8"))
            openclaw_index = writer.vault_path / "Topics/OpenClaw/OpenClaw Index.md"
            self.assertIn("[[Inbox/OpenClaw/", openclaw_index.read_text(encoding="utf-8"))

    def test_note_frontmatter_uses_canonical_source_url_and_no_comment_dup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            note = writer.write(
                SummaryResult(
                    title="来源链接规范化",
                    primary_topic="AI",
                    secondary_topics=[],
                    entities=[],
                    conclusion="完成。",
                    bullets=["安装方法：直接将GitHub链接丢给OpenClaw"],
                    evidence_quotes=["安装方法：直接将GitHub链接丢给OpenClaw"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[],
                ),
                EvidenceBundle(
                    source_kind="url",
                    source_url="https://www.xiaohongshu.com/explore/abc123?xsec_token=xyz&share_id=1",
                    platform_hint="xiaohongshu",
                    title="原始标题",
                    text="正文",
                    evidence_type="visible_page_text",
                    coverage="full",
                ),
            )
            content = (writer.vault_path / str(note["note_path"])).read_text(encoding="utf-8")
            self.assertIn("source_url: https://www.xiaohongshu.com/explore/abc123", content)
            self.assertIn("## 来源", content)
            self.assertIn("- https://www.xiaohongshu.com/explore/abc123", content)
            self.assertNotIn("xsec_token=", content)
            self.assertNotIn("<!-- source_url:", content)

    def test_note_frontmatter_keeps_youtube_video_id_and_strips_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            note = writer.write(
                SummaryResult(
                    title="YouTube 链接规范化",
                    primary_topic="AI",
                    secondary_topics=[],
                    entities=[],
                    conclusion="完成。",
                    bullets=["视频链接"],
                    evidence_quotes=["视频链接"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[],
                ),
                EvidenceBundle(
                    source_kind="video_url",
                    source_url="https://www.youtube.com/watch?v=c7qJzG_swUE&utm_source=test&share_id=1",
                    platform_hint="youtube",
                    title="视频标题",
                    text="正文",
                    evidence_type="multimodal_video",
                    coverage="full",
                ),
            )
            content = (writer.vault_path / str(note["note_path"])).read_text(encoding="utf-8")
            self.assertIn("source_url: https://www.youtube.com/watch?v=c7qJzG_swUE", content)
            self.assertNotIn("utm_source=", content)
            self.assertNotIn("share_id=", content)

    def test_video_note_keeps_fallback_evidence_quotes_when_no_scored_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            note = writer.write(
                SummaryResult(
                    title="视频测试",
                    primary_topic="视频",
                    secondary_topics=[],
                    entities=[],
                    conclusion="完成。",
                    bullets=["视频链接: https://www.youtube.com/watch?v=abc"],
                    evidence_quotes=[],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[],
                ),
                EvidenceBundle(
                    source_kind="video_url",
                    source_url="https://www.youtube.com/watch?v=abc",
                    platform_hint="youtube",
                    title="原始标题",
                    text="今天我们聊一下如何从零搭建自动化工作流。\n这期内容会覆盖模型选择与成本控制。\n最后给一个可直接执行的检查清单。",
                    evidence_type="multimodal_video",
                    coverage="full",
                ),
            )
            content = (writer.vault_path / str(note["note_path"])).read_text(encoding="utf-8")
            self.assertIn("## 关键证据", content)
            self.assertIn("今天我们聊一下如何从零搭建自动化工作流。", content)
            self.assertNotIn("（无可提取正文）", content)

    def test_explainer_paragraph_length_is_bounded_and_natural(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            note = writer.write(
                SummaryResult(
                    title="美股财报分析 Skill",
                    primary_topic="美股财报分析",
                    secondary_topics=["技能推荐"],
                    entities=[],
                    conclusion="可用。",
                    bullets=[
                        "安装方法：直接将GitHub链接丢给OpenClaw",
                        "读取公司最新财报并更新估值矩阵",
                        "输出完整决策框架",
                    ],
                    evidence_quotes=["安装方法：直接将GitHub链接丢给OpenClaw"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[],
                ),
                EvidenceBundle(
                    source_kind="url",
                    source_url="https://github.com/star23/Day1Global-Skills",
                    platform_hint="github",
                    title="原始标题",
                    text="正文",
                    evidence_type="structured_github_text",
                    coverage="full",
                    metadata={
                        "signals": {
                            "skills": ["美股财报深度分析 Skill"],
                            "projects": ["star23/Day1Global-Skills"],
                        }
                    },
                ),
            )
            content = (writer.vault_path / str(note["note_path"])).read_text(encoding="utf-8")
            match = re.search(r"## 专业解读\n(.+?)\n\n", content, re.S)
            self.assertIsNotNone(match)
            paragraph = match.group(1).strip()
            visible_length = len(paragraph.replace("**", ""))
            self.assertGreaterEqual(visible_length, 55)
            self.assertLessEqual(visible_length, 140)
            self.assertIn("现在最该记住的两点是", paragraph)
            self.assertNotIn("给不熟悉的人", paragraph)
            self.assertNotIn("对你最有用的是", paragraph)

    def test_explainer_paragraph_for_video_fallback_is_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            preview = writer.preview(
                SummaryResult(
                    title="未命名内容",
                    primary_topic="未分类",
                    secondary_topics=[],
                    entities=[],
                    conclusion="模型不可用，以下内容为原始证据的抽取摘要（未进行推断）。",
                    bullets=[
                        "[视频页面补充]",
                        "00:02 / 00:47",
                        "平民版彭博终端来了！我用开源situation monitor把全球新闻+金融+地缘热点一屏看完",
                    ],
                    evidence_quotes=["平民版彭博终端来了"],
                    coverage="partial",
                    confidence="medium",
                    note_tags=[],
                    follow_up_actions=[],
                ),
                EvidenceBundle(
                    source_kind="video_url",
                    source_url="https://www.bilibili.com/video/BV1HpP5zBEEp",
                    platform_hint="bilibili",
                    title="视频标题",
                    text="视频页面文本",
                    evidence_type="video_page_snapshot",
                    coverage="partial",
                ),
            )
            content = str(preview["content"])
            match = re.search(r"## 专业解读\n(.+?)\n\n", content, re.S)
            self.assertIsNotNone(match)
            paragraph = match.group(1).strip()
            visible_length = len(paragraph.replace("**", ""))
            self.assertGreaterEqual(visible_length, 40)
            self.assertLessEqual(visible_length, 140)
            self.assertIn("当前更适合先放进待筛选清单", paragraph)
            self.assertNotIn("可直接安装到 OpenClaw", paragraph)
            self.assertNotIn("财报", paragraph)
            self.assertNotIn("对你最有用的是", paragraph)

    def test_priority_links_use_generic_label_for_non_github(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            preview = writer.preview(
                SummaryResult(
                    title="OpenClaw 文档主页",
                    primary_topic="AI",
                    secondary_topics=[],
                    entities=[],
                    conclusion="已提取核心事实。",
                    bullets=["介绍 OpenClaw 的安装与使用方式。"],
                    evidence_quotes=["Install OpenClaw and bring up the service"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[],
                ),
                EvidenceBundle(
                    source_kind="url",
                    source_url="https://docs.openclaw.ai/",
                    platform_hint="docs",
                    title="OpenClaw 文档",
                    text="文档正文",
                    evidence_type="visible_page_text",
                    coverage="full",
                    metadata={"signals": {"links": ["https://docs.openclaw.ai/"]}},
                ),
            )
            content = str(preview["content"])
            self.assertIn("## 项目与链接", content)
            self.assertIn("- 关键链接: https://docs.openclaw.ai/", content)
            self.assertNotIn("- GitHub地址: https://docs.openclaw.ai/", content)

    def test_follow_up_actions_render_execution_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            preview = writer.preview(
                SummaryResult(
                    title="安装教学",
                    primary_topic="AI",
                    secondary_topics=[],
                    entities=[],
                    conclusion="可按步骤安装。",
                    bullets=["项目名称: star23/Day1Global-Skills", "技能ID: tech-earnings-deepdive", "GitHub地址: https://github.com/star23/Day1Global-Skills"],
                    evidence_quotes=["/install-skill"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[
                        "执行命令：/install-skill https://github.com/Day1Global/Day1Global-Skills/raw/main/tech-earnings-deepdive.skill",
                        "验证技能已在对话中自动激活",
                    ],
                ),
                EvidenceBundle(
                    source_kind="url",
                    source_url="https://github.com/star23/Day1Global-Skills",
                    platform_hint="github",
                    title="安装教学",
                    text="正文",
                    evidence_type="structured_github_text",
                    coverage="full",
                ),
            )
            content = str(preview["content"])
            self.assertIn("## 执行清单", content)
            self.assertIn("/install-skill", content)
            self.assertIn("验证技能已在对话中自动激活", content)

    def test_video_preview_includes_reliability_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            preview = writer.preview(
                SummaryResult(
                    title="视频案例",
                    primary_topic="AI",
                    secondary_topics=[],
                    entities=[],
                    conclusion="当前证据不完整。",
                    bullets=["视频链接: https://www.bilibili.com/video/BV1tyNNzxEpK", "主题: 某个视频"],
                    evidence_quotes=["关键片段"],
                    coverage="partial",
                    confidence="medium",
                    note_tags=[],
                    follow_up_actions=["补抓字幕或语音轨后再复核结论"],
                ),
                EvidenceBundle(
                    source_kind="video_url",
                    source_url="https://www.bilibili.com/video/BV1tyNNzxEpK",
                    platform_hint="bilibili",
                    title="视频案例",
                    text="[视频时间线要点]\n[00:02] 第一段关键结论",
                    evidence_type="multimodal_video",
                    coverage="partial",
                    metadata={
                        "evidence_sources": ["video_platform_metadata", "video_audio_asr"],
                        "tracks": {
                            "has_subtitle": False,
                            "has_transcript": True,
                            "has_keyframes": True,
                            "has_keyframe_ocr": True,
                        },
                        "video_gate_reasons": ["missing speech track (subtitle/transcript)"],
                    },
                ),
            )
            content = str(preview["content"])
            self.assertIn("## 可信度与局限", content)
            self.assertIn("证据来源:", content)
            self.assertIn("轨道状态:", content)
            self.assertIn("建议动作:", content)

    def test_video_mind_map_prefers_fact_focus_and_clean_next_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            preview = writer.preview(
                SummaryResult(
                    title="开源项目使用视频教程",
                    primary_topic="开源项目",
                    secondary_topics=[],
                    entities=[],
                    conclusion="视频的核心意思是：了解项目功能和技术点是入手的第一步；同时补充如何下载和运行开源项目。",
                    bullets=[
                        "视频链接: https://www.bilibili.com/video/BV1y4411p74E",
                        "了解项目功能和技术点是入手的第一步",
                        "视频中介绍了如何下载和运行开源项目",
                    ],
                    evidence_quotes=["怎么来完整一个开源项目"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=["尝试下载并运行推荐的Hello项目"],
                ),
                EvidenceBundle(
                    source_kind="video_url",
                    source_url="https://www.bilibili.com/video/BV1y4411p74E",
                    platform_hint="bilibili",
                    title="视频演示如何玩转一个开源项目",
                    text="正文",
                    evidence_type="multimodal_video",
                    coverage="full",
                    metadata={
                        "signals": {
                            "links": ["https://www.bilibili.com/video/BV1y4411p74E"]
                        },
                        "content_profile": {
                            "kind": "video_explainer",
                        },
                    },
                ),
            )
            content = str(preview["content"])
            self.assertIn("├─ 核心对象: 了解项目功能和技术点是入手的第一步", content)
            self.assertIn("├─ 下一步: 尝试下载并运行推荐的Hello项目", content)
            self.assertIn("## 核心事实\n- 了解项目功能和技术点是入手的第一步", content)

    def test_note_frontloads_mind_map_usefulness_and_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["技能推荐", "GitHub"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            preview = writer.preview(
                SummaryResult(
                    title="Skill 安装说明",
                    primary_topic="技能推荐",
                    secondary_topics=["GitHub"],
                    entities=["tech-earnings-deepdive"],
                    conclusion="该技能可直接安装使用。",
                    bullets=[
                        "项目名称: star23/Day1Global-Skills",
                        "GitHub地址: https://github.com/star23/Day1Global-Skills",
                        "安装方法: /install-skill https://github.com/Day1Global/Day1Global-Skills/raw/main/tech-earnings-deepdive.skill",
                    ],
                    evidence_quotes=["/install-skill"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=["执行命令：/install-skill https://github.com/Day1Global/Day1Global-Skills/raw/main/tech-earnings-deepdive.skill"],
                ),
                EvidenceBundle(
                    source_kind="pasted_text",
                    source_url=None,
                    platform_hint=None,
                    title="Skill 安装说明",
                    text="推荐一个 Skill，安装命令已经给出。",
                    evidence_type="raw_text",
                    coverage="full",
                    metadata={
                        "content_profile": {
                            "kind": "skill_recommendation",
                            "required_signal_keys": ["projects", "links", "skill_ids", "commands"],
                            "optional_signal_keys": [],
                            "require_action_checklist": True,
                            "require_project_section": True,
                        },
                        "signals": {
                            "projects": ["star23/Day1Global-Skills"],
                            "links": ["https://github.com/star23/Day1Global-Skills"],
                            "skills": ["美股财报深度分析 Skill"],
                            "skill_ids": ["tech-earnings-deepdive"],
                            "commands": ["/install-skill https://github.com/Day1Global/Day1Global-Skills/raw/main/tech-earnings-deepdive.skill"],
                        },
                    },
                ),
            )
            content = str(preview["content"])
            self.assertIn("## 文字脑图", content)
            self.assertIn("## 对你有什么用", content)
            self.assertIn("## 关键词", content)
            self.assertIn("**技能推荐**", content)

    def test_note_renders_secretary_judgment_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            preview = writer.preview(
                SummaryResult(
                    title="项目判断",
                    primary_topic="AI",
                    secondary_topics=[],
                    entities=[],
                    conclusion="这个项目值得关注。",
                    bullets=["项目名称: demo/project", "GitHub地址: https://github.com/demo/project", "适合快速试跑"],
                    evidence_quotes=["适合快速试跑"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[],
                    timeliness="high",
                    effectiveness="medium",
                    recommendation_level="recommended",
                    reader_judgment="从大厂程序员视角看，这条内容值得收藏，适合后续试跑。",
                ),
                EvidenceBundle(
                    source_kind="url",
                    source_url="https://github.com/demo/project",
                    platform_hint="github",
                    title="项目判断",
                    text="正文",
                    evidence_type="visible_page_text",
                    coverage="full",
                ),
            )
            content = str(preview["content"])
            self.assertIn("## 贾维斯判断", content)
            self.assertIn("适用身份: 大厂程序员", content)
            self.assertIn("时效性: 高", content)
            self.assertIn("有效程度: 中", content)
            self.assertIn("推荐等级: 建议看", content)
            self.assertIn("从大厂程序员视角看，这条内容值得收藏", content)


if __name__ == "__main__":
    unittest.main()
