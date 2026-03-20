import tempfile
import unittest
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.config import ObsidianConfig
from openclaw_capture_workflow.models import EvidenceBundle, SummaryResult
from openclaw_capture_workflow.obsidian import ObsidianWriter


class _FakeNoteRenderer:
    def render(self, materials):
        lines = [f"# {materials['title']}"]
        lines.append("")
        lines.append(materials.get("summary", {}).get("conclusion", ""))
        evidence_text = str(materials.get("evidence", {}).get("text", "")).strip()
        if evidence_text:
            lines.append("")
            lines.append(evidence_text[:300])
        for item in materials.get("summary", {}).get("evidence_quotes", [])[:2]:
            text = str(item).strip()
            if text:
                lines.append("")
                lines.append(text)
        for item in materials.get("summary", {}).get("follow_up_actions", [])[:2]:
            text = str(item).strip()
            if text:
                lines.append("")
                lines.append(text)
        for item in materials.get("fragments", {}).get("commands", [])[:2]:
            text = str(item).strip()
            if text:
                lines.append("")
                lines.append(text)
        commands = materials.get("fragments", {}).get("commands", [])
        if commands:
            lines.append("")
            lines.extend(str(item) for item in commands[:2])
        warnings = materials.get("warnings", [])
        if warnings:
            lines.append("")
            lines.extend(str(item) for item in warnings[:2])
        source_url = materials.get("source", {}).get("source_url", "")
        if source_url:
            lines.append("")
            lines.append(f"- {source_url}")
        return "\n".join(line for line in lines if line is not None).strip() + "\n"


class _StaticNoteRenderer:
    def __init__(self, content: str) -> None:
        self.content = content

    def render(self, materials):
        return self.content


def _writer(config: ObsidianConfig) -> ObsidianWriter:
    materials_root = Path(config.vault_path).expanduser() / "_materials"
    return ObsidianWriter(config, renderer=_FakeNoteRenderer(), materials_root=materials_root)


class ObsidianWriterTest(unittest.TestCase):
    def test_write_creates_main_note_and_topic_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
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
            self.assertEqual(note["keyword_l1"], "股票投资")
            self.assertIn("[[Topics/_Keywords/股票投资/股票投资 Index.md]]", note["keyword_links"])
            self.assertTrue((writer.vault_path / "Topics/_Keywords/股票投资/股票投资 Index.md").exists())
            self.assertFalse((writer.vault_path / "Entities/OpenAI.md").exists())

    def test_non_whitelist_topics_do_not_auto_create_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
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
            writer = _writer(
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
            writer = _writer(
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
            writer = _writer(
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
            writer = _writer(
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
            writer = _writer(
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
            self.assertIn("https://www.xiaohongshu.com/explore/abc123", content)
            self.assertNotIn("xsec_token=", content)
            self.assertNotIn("<!-- source_url:", content)

    def test_note_frontmatter_keeps_youtube_video_id_and_strips_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
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
            writer = _writer(
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
            self.assertIn("今天我们聊一下如何从零搭建自动化工作流。", content)
            self.assertNotIn("## 一句话总结", content)
            self.assertNotIn("## 核心事实", content)

    def test_note_drops_old_template_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
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
            self.assertIn("可用。", content)
            self.assertNotIn("## 一句话总结", content)
            self.assertNotIn("## 文字脑图", content)
            self.assertNotIn("## 贾维斯判断", content)
            self.assertNotIn("## 专业解读", content)

    def test_video_fallback_note_keeps_evidence_without_old_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
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
            self.assertIn("模型不可用", content)
            self.assertIn("平民版彭博终端来了", content)
            self.assertNotIn("## 专业解读", content)

    def test_priority_links_use_generic_label_for_non_github(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
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
            self.assertIn("- https://docs.openclaw.ai/", content)
            self.assertNotIn("## 项目与链接", content)

    def test_follow_up_actions_render_execution_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
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
            self.assertIn("/install-skill", content)
            self.assertIn("验证技能已在对话中自动激活", content)

    def test_video_preview_includes_reliability_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
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
            self.assertIn("当前证据不完整", content)
            self.assertIn("https://www.bilibili.com/video/BV1tyNNzxEpK", content)

    def test_video_note_keeps_core_fact_without_old_mind_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
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
            self.assertIn("了解项目功能和技术点是入手的第一步", content)
            self.assertNotIn("## 文字脑图", content)

    def test_note_drops_mind_map_and_keywords_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
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
            self.assertIn("该技能可直接安装使用。", content)
            self.assertNotIn("## 文字脑图", content)
            self.assertNotIn("## 对你有什么用", content)
            self.assertNotIn("## 关键词", content)

    def test_note_drops_secretary_judgment_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
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
                    reader_judgment="这条内容值得收藏，适合后续试跑。",
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
            self.assertIn("这个项目值得关注。", content)
            self.assertNotIn("## 贾维斯判断", content)
            self.assertNotIn("适用身份: 大厂程序员", content)

    def test_non_tutorial_strips_legacy_next_steps_heading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ObsidianConfig(
                vault_path=tmp,
                inbox_root="Inbox/OpenClaw",
                topics_root="Topics",
                entities_root="Entities",
                auto_topic_whitelist=["AI"],
                auto_topic_blocklist=[],
                auto_entity_pages=False,
            )
            writer = ObsidianWriter(
                config,
                renderer=_StaticNoteRenderer(
                    "# 股票专用龙虾开源项目总结\n\n项目值得关注。\n\n### 可直接做的下一步\n- 访问项目链接了解更多信息。\n- 评估该系统在当前项目中的适用性。\n"
                ),
                materials_root=Path(tmp) / "_materials",
            )
            preview = writer.preview(
                SummaryResult(
                    title="股票专用龙虾开源项目总结",
                    primary_topic="AI",
                    secondary_topics=[],
                    entities=[],
                    conclusion="项目值得关注。",
                    bullets=["项目值得关注。"],
                    evidence_quotes=[],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=["访问项目链接了解更多信息。", "评估该系统在当前项目中的适用性。"],
                ),
                EvidenceBundle(
                    source_kind="url",
                    source_url="https://www.xiaohongshu.com/explore/69b41a4c000000002103b520",
                    platform_hint="xiaohongshu",
                    title="股票专用龙虾开源项目总结",
                    text="正文",
                    evidence_type="visible_page_text",
                    coverage="full",
                ),
            )
            content = str(preview["content"])
            self.assertNotIn("### 贾维斯的思考", content)
            self.assertNotIn("可直接做的下一步", content)
            self.assertNotIn("如果我是你", content)
            self.assertIn("- 访问项目链接了解更多信息。", content)
            self.assertIn("- 评估该系统在当前项目中的适用性。", content)

    def test_note_frontmatter_includes_keyword_hierarchy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "网络安全"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            note = writer.write(
                SummaryResult(
                    title="摄像头0day漏洞挖掘入门",
                    primary_topic="网络安全",
                    secondary_topics=["IoT安全", "逆向工程"],
                    entities=["DCS935L"],
                    conclusion="这是一条IoT漏洞挖掘入门视频。",
                    bullets=["IoT设备漏洞挖掘", "使用Kali和IDA", "教学用途"],
                    evidence_quotes=[],
                    coverage="full",
                    confidence="high",
                    note_tags=["0day"],
                    follow_up_actions=[],
                ),
                EvidenceBundle(
                    source_kind="video_url",
                    source_url="https://www.bilibili.com/video/BV19VAUzUEL6",
                    platform_hint="bilibili",
                    title="摄像头0day漏洞挖掘入门",
                    text="正文",
                    evidence_type="multimodal_video",
                    coverage="full",
                ),
            )
            content = (writer.vault_path / str(note["note_path"])).read_text(encoding="utf-8")
            self.assertIn("keyword_l1: 网络安全", content)
            self.assertIn("keyword_l2: IoT安全,逆向工程,DCS935L,0day", content)
            self.assertIn("tags:\n  - \"0day\"\n  - \"网络安全\"\n  - \"IoT安全\"\n  - \"逆向工程\"\n  - \"DCS935L\"", content)
            self.assertTrue((writer.vault_path / "Topics/_Keywords/网络安全/IoT安全.md").exists())

    def test_note_frontmatter_tags_dedupe_and_preserve_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "股票"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            note = writer.write(
                SummaryResult(
                    title="标签顺序检查",
                    primary_topic="AI",
                    secondary_topics=["股票"],
                    entities=[],
                    conclusion="测试结论。",
                    bullets=["AI 产品更新", "资本市场反应"],
                    evidence_quotes=[],
                    coverage="full",
                    confidence="high",
                    note_tags=["AI", "股票", "AI", "0day"],
                    follow_up_actions=[],
                ),
                EvidenceBundle(
                    source_kind="url",
                    source_url="https://example.com/x",
                    platform_hint="web",
                    title="原始标题",
                    text="正文",
                    evidence_type="visible_page_text",
                    coverage="full",
                ),
            )
            content = (writer.vault_path / str(note["note_path"])).read_text(encoding="utf-8")
            self.assertIn("tags:\n  - \"AI\"\n  - \"股票\"\n  - \"0day\"\n  - \"网络安全\"", content)

    def test_note_frontmatter_omits_tags_when_no_note_or_keyword_tags_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=[],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                ),
                renderer=_StaticNoteRenderer("# 无标签\n\n正文\n"),
                materials_root=Path(tmp) / "_materials",
            )
            note = writer.write(
                SummaryResult(
                    title="无标签",
                    primary_topic="未分类",
                    secondary_topics=[],
                    entities=[],
                    conclusion="测试结论。",
                    bullets=["普通内容"],
                    evidence_quotes=[],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[],
                ),
                EvidenceBundle(
                    source_kind="url",
                    source_url="https://example.com/no-tags",
                    platform_hint="web",
                    title="原始标题",
                    text="正文",
                    evidence_type="visible_page_text",
                    coverage="full",
                ),
            )
            content = (writer.vault_path / str(note["note_path"])).read_text(encoding="utf-8")
            self.assertNotIn("\ntags:\n", content)

    def test_video_note_bypasses_generic_renderer_and_uses_direct_video_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = ObsidianWriter(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "股票", "产品体验"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                ),
                renderer=_StaticNoteRenderer("# 模板化失败内容\n\n这段视频详细盘点了..."),
                materials_root=Path(tmp) / "_materials",
            )
            note = writer.write(
                SummaryResult(
                    title="OpenClaw你虾哥每天股票量化交易推荐",
                    primary_topic="视频",
                    secondary_topics=[],
                    entities=["OpenClaw"],
                    conclusion="视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议；同时流程是把自选股列表交给 OpenClaw，系统会在开盘前给出逐只股票的分析和买入/持有建议，整体更偏技术展示而非直接投资建议。",
                    bullets=[
                        "1. 视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议。",
                        "2. 流程是把自选股列表交给 OpenClaw，系统会在开盘前给出逐只股票的分析和买入/持有建议。",
                        "3. 实现上依赖 GitHub、服务器或自动化工作流，把整套分析流程持续跑起来。",
                    ],
                    evidence_quotes=[],
                    coverage="full",
                    confidence="high",
                    note_tags=["OpenClaw", "量化交易"],
                    follow_up_actions=[],
                ),
                EvidenceBundle(
                    source_kind="video_url",
                    source_url="https://www.bilibili.com/video/BV1bFPMzFEnd",
                    platform_hint="bilibili",
                    title="OpenClaw你虾哥每天股票量化交易推荐",
                    text="视频证据",
                    transcript="把自选股交给 OpenClaw，在开盘前给出买入或者持有建议。",
                    evidence_type="multimodal_video",
                    coverage="full",
                    metadata={
                        "video_story_blocks": [
                            {"label": "core_topic", "summary": "视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议。", "evidence": []},
                            {"label": "workflow", "summary": "流程是把自选股列表交给 OpenClaw，系统会在开盘前给出逐只股票的分析和买入/持有建议。", "evidence": []},
                            {"label": "implementation", "summary": "实现上依赖 GitHub、服务器或自动化工作流，把整套分析流程持续跑起来。", "evidence": []},
                        ],
                    },
                ),
            )
            content = (writer.vault_path / str(note["note_path"])).read_text(encoding="utf-8")
            self.assertIn("这个视频大意是在演示：作者怎么把 OpenClaw 改造成一个", content)
            self.assertNotIn("这段视频详细盘点了", content)

    def test_keyword_hierarchy_prefers_product_experience_for_interaction_design_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["产品体验"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            note = writer.write(
                SummaryResult(
                    title="简中互联网十大糟糕交互设计盘点",
                    primary_topic="视频",
                    secondary_topics=["用户体验"],
                    entities=[],
                    conclusion="这是一条交互设计吐槽视频。",
                    bullets=["交互设计反模式", "产品经理为了 KPI 牺牲用户体验"],
                    evidence_quotes=[],
                    coverage="full",
                    confidence="high",
                    note_tags=["交互设计", "产品经理", "KPI驱动", "简中互联网"],
                    follow_up_actions=[],
                ),
                EvidenceBundle(
                    source_kind="video_url",
                    source_url="https://www.bilibili.com/video/BV1WAcQzKEW8",
                    platform_hint="bilibili",
                    title="简中互联网十大糟糕交互设计盘点",
                    text="简中互联网 交互设计 用户体验 产品经理 KPI驱动",
                    evidence_type="multimodal_video",
                    coverage="full",
                ),
            )
            content = (writer.vault_path / str(note["note_path"])).read_text(encoding="utf-8")
            self.assertIn("keyword_l1: 产品体验", content)

    def test_keyword_hierarchy_does_not_infer_stock_from_synthetic_video_bullets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["求职职场", "股票投资"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            note = writer.write(
                SummaryResult(
                    title="求职面试中，最常见的12个问题",
                    primary_topic="求职面试常见问题及应对策略",
                    secondary_topics=["职业规划", "自我介绍"],
                    entities=["面试问题"],
                    conclusion="内容聚焦求职面试准备。",
                    bullets=[
                        "1. 视频把关键流程拆成了输入、配置和运行几个环节，重点在把方案真正跑起来。",
                        "2. 系统会结合行情、业绩和多种数据源来做判断，而不只是给一句结论。",
                    ],
                    evidence_quotes=[],
                    coverage="partial",
                    confidence="medium",
                    note_tags=[],
                    follow_up_actions=[],
                ),
                EvidenceBundle(
                    source_kind="video_url",
                    source_url="https://www.bilibili.com/video/BV1ggpcevEgk",
                    platform_hint="bilibili",
                    title="求职面试中，最常见的12个问题",
                    text="标签: 求职 面试 职场",
                    evidence_type="multimodal_video",
                    coverage="partial",
                    metadata={
                        "bilibili_tags": ["求职", "面试", "职场"],
                    },
                ),
            )
            content = (writer.vault_path / str(note["note_path"])).read_text(encoding="utf-8")
            self.assertIn("keyword_l1: 求职职场", content)
            self.assertNotIn("keyword_l1: 股票投资", content)

    def test_blocked_video_preview_hides_raw_debug_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = ObsidianConfig(
                vault_path=tmp,
                inbox_root="Inbox/OpenClaw",
                topics_root="Topics",
                entities_root="Entities",
                auto_topic_whitelist=["AI"],
                auto_topic_blocklist=[],
                auto_entity_pages=False,
            )
            writer = ObsidianWriter(
                config,
                renderer=_StaticNoteRenderer(
                    "# 小红书页面丢失\n\nPython 3.9 已弃用，Unsupported URL，yt-dlp 失败。\n\n### 可直接做的下一步\n- 更新 Python\n- 尝试其他工具\n"
                ),
                materials_root=Path(tmp) / "_materials",
            )
            preview = writer.preview(
                SummaryResult(
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
                EvidenceBundle(
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
            )
            content = str(preview["content"])
            self.assertIn("当前结果不能作为内容总结使用", content)
            self.assertIn("不建议继续基于这版结果判断内容", content)
            self.assertNotIn("## 贾维斯的思考", content)
            self.assertNotIn("如果我是你", content)
            self.assertNotIn("Python 3.9", content)
            self.assertNotIn("Unsupported URL", content)
            self.assertNotIn("yt-dlp", content)

    def test_long_finance_video_preview_uses_briefing_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["股票投资"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            preview = writer.preview(
                SummaryResult(
                    title="第1143日投资记录",
                    primary_topic="投资记录",
                    secondary_topics=["港股"],
                    entities=["中国食品", "海底捞"],
                    conclusion="视频核心是在复盘当前市场判断、持仓逻辑与后续调仓计划。",
                    bullets=[
                        "市场赚钱效应一般，港股结构尚可。",
                        "重点讨论海底捞、中国食品、腾讯控股等标的。",
                    ],
                    evidence_quotes=["市场赚钱效应一般"],
                    coverage="full",
                    confidence="high",
                    note_tags=["投资", "港股"],
                    follow_up_actions=[],
                    reader_judgment="适合当回看前的结构化提纲，但具体买卖仍要回到原视频复核。",
                    timeline_sections=[
                        {
                            "start": 1.0,
                            "end": 371.0,
                            "heading": "投资哲学与策略框架",
                            "summary": "前半段先讲投资体系、收益目标和当前对市场的基本看法。",
                            "bullets": ["强调防御性强、市场大跌时回撤小。", "今年目标是稳住节奏，不做高波动博弈。"],
                            "evidence": ["[00:01] 投资哲学与策略框架"],
                        },
                        {
                            "start": 371.0,
                            "end": 975.0,
                            "heading": "持仓标的的逻辑与估值对比",
                            "summary": "中段逐个展开海底捞、中国食品、腾讯控股等持仓或观察标的。",
                            "bullets": ["中国食品估值更低，资金从海底捞切换至此。", "海底捞剩余 2500 股，计划冲高卖出。"],
                            "evidence": ["[06:11] 持仓标的的逻辑与估值对比"],
                        },
                        {
                            "start": 975.0,
                            "end": 1741.0,
                            "heading": "风险控制、业绩回顾与后续计划",
                            "summary": "尾段总结今年收益、市场风险和接下来的调仓思路。",
                            "bullets": ["今年组合跑赢恒指和恒科。", "后续以低频调仓为主，不追高。"],
                            "evidence": ["[16:15] 风险控制、业绩回顾与后续计划"],
                        },
                    ],
                    finance_matrix=[
                        {
                            "name": "海底捞",
                            "sector": "消费/餐饮",
                            "thesis": "估值 19 倍 PE，业绩反身性强，但估值已不便宜。",
                            "position_change": "剩余 2500 股，计划若再涨则卖出。",
                            "risk": "估值溢价风险，业绩反身性。",
                        },
                        {
                            "name": "中国食品",
                            "sector": "消费/饮料",
                            "thesis": "估值 10 倍 PE 左右，现金充足，产品矩阵稳定。",
                            "position_change": "资金从海底捞切换至此，持仓中。",
                            "risk": "节奏偏慢，但估值优势明显。",
                        },
                    ],
                    finance_snapshot={
                        "market_view": ["市场赚钱效应一般，但港股结构尚可。"],
                        "performance_review": ["今年组合跑赢恒指和恒科。"],
                        "action_plan": ["后续以低频调仓为主，不追高。"],
                    },
                ),
                EvidenceBundle(
                    source_kind="video_url",
                    source_url="https://www.bilibili.com/video/BV1unw9zxEHF",
                    platform_hint="bilibili",
                    title="第1143日投资记录",
                    text="市场赚钱效应一般，重点讨论海底捞和中国食品。",
                    evidence_type="multimodal_video",
                    coverage="full",
                    metadata={
                        "video_duration_seconds": 1741.0,
                        "video_direction_estimate": {"kind": "finance_market"},
                        "evidence_sources": ["video_platform_metadata", "video_audio_asr", "video_keyframe_ocr"],
                        "tracks": {
                            "has_subtitle": False,
                            "has_transcript": True,
                            "has_keyframes": True,
                            "has_keyframe_ocr": True,
                        },
                    },
                ),
            )
            content = str(preview["content"])
            self.assertIn("## 核心判断", content)
            self.assertIn("## 时间线附录", content)
            self.assertIn("## 标的矩阵", content)
            self.assertIn("| 股票名称 | 板块 | 投资逻辑/估值情况 | 涨跌/持仓变动 | 预期/风险点 |", content)
            self.assertIn("## 标的卡片", content)
            self.assertIn("### 海底捞", content)
            self.assertIn("## 市场判断与后续计划", content)
            self.assertIn("## 可信度与证据", content)
            self.assertNotIn("## 时间段速览", content)
            self.assertNotIn("## 一句话总结", content)
            self.assertNotIn("## 执行清单", content)
            self.assertLess(content.index("## 核心判断"), content.index("## 市场判断与后续计划"))
            self.assertLess(content.index("## 市场判断与后续计划"), content.index("## 标的矩阵"))
            self.assertLess(content.index("## 标的卡片"), content.index("## 时间线附录"))
            self.assertLess(content.index("## 时间线附录"), content.index("## 可信度与证据"))

    def test_long_non_finance_video_preview_skips_finance_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = _writer(
                ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["求职职场"],
                    auto_topic_blocklist=[],
                    auto_entity_pages=False,
                )
            )
            preview = writer.preview(
                SummaryResult(
                    title="面试后等待流程解析",
                    primary_topic="面试流程",
                    secondary_topics=[],
                    entities=[],
                    conclusion="视频按时间顺序解释了面试后等待 offer 的内部流转。",
                    bullets=["面试官先写评价表", "之后还要经过用人部门内部对齐"],
                    evidence_quotes=["面试官并不会告诉你HR发的结论"],
                    coverage="full",
                    confidence="high",
                    note_tags=[],
                    follow_up_actions=[],
                    reader_judgment="适合保留作求职流程参考，但不需要财经矩阵那类结构。",
                    timeline_sections=[
                        {"start": 0.0, "end": 120.0, "heading": "等待期心理落差", "summary": "开头描述候选人在等待期的典型情绪。", "bullets": ["第 1-3 天在等面试官写评价。"], "evidence": []},
                        {"start": 120.0, "end": 240.0, "heading": "面试评价表", "summary": "中段解释面试官如何写评价表以及为什么会拖延。", "bullets": ["面试官不写，HR 就推不动流程。"], "evidence": []},
                        {"start": 240.0, "end": 360.0, "heading": "用人部门内部对齐", "summary": "后段解释多轮面试后如何内部对齐和定级。", "bullets": ["leader 可能会拉会讨论候选人排名。"], "evidence": []},
                    ],
                ),
                EvidenceBundle(
                    source_kind="video_url",
                    source_url="https://www.xiaohongshu.com/explore/69afbf57000000001d027f5a",
                    platform_hint="xiaohongshu",
                    title="面试后等待流程解析",
                    text="面试官并不会告诉你HR发的结论。",
                    evidence_type="multimodal_video",
                    coverage="full",
                    metadata={
                        "video_duration_seconds": 920.0,
                        "video_direction_estimate": {"kind": "career_interview"},
                        "evidence_sources": ["video_audio_asr", "video_keyframe_ocr"],
                    },
                ),
            )
            content = str(preview["content"])
            self.assertIn("## 核心判断", content)
            self.assertIn("## 内容主线与用途", content)
            self.assertIn("## 适用边界与风险", content)
            self.assertIn("## 时间线附录", content)
            self.assertIn("## 可信度与证据", content)
            self.assertNotIn("## 标的矩阵", content)
            self.assertNotIn("## 标的卡片", content)
            self.assertNotIn("## 市场判断与后续计划", content)
            self.assertLess(content.index("## 核心判断"), content.index("## 时间线附录"))
            self.assertLess(content.index("## 时间线附录"), content.index("## 可信度与证据"))


if __name__ == "__main__":
    unittest.main()
