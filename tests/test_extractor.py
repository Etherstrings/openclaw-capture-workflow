import json
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.config import AppConfig, ExtractorConfig, ObsidianConfig, SummarizerConfig, TelegramConfig
from openclaw_capture_workflow.extractor import (
    _cleanup_browser_tab,
    _canonicalize_video_source_url,
    _extract_high_value_ocr_lines,
    _extract_article_blocks,
    _fetch_bilibili_video_metadata,
    _extract_meta_description,
    _extract_bilibili_viewer_feedback_from_snapshot,
    _extract_skill_signals,
    _extract_text_from_browser_snapshot,
    _extract_steps_from_tencent_snapshot,
    _extract_text_from_tencent_snapshot,
    _normalize_structured_ocr_output,
    _parse_video_text_output,
    _extract_wechat_article,
    _find_browser_tab_for_url,
    _find_or_open_browser_tab_with_state,
    _looks_like_legal_footer,
    _looks_like_command_line,
    _should_try_browser_ocr,
    _sanitize_video_page_snapshot_text,
    _split_user_guidance_from_evidence,
    _github_blob_from_url,
)
from openclaw_capture_workflow.models import IngestRequest
from openclaw_capture_workflow.extractor import EvidenceExtractor
from openclaw_capture_workflow.analyzer.models import AnalysisOutcome, SectionResult, StructuredDocument


def _config(tmp: str) -> AppConfig:
    return AppConfig(
        listen_host="127.0.0.1",
        listen_port=8765,
        state_dir="state",
        obsidian=ObsidianConfig(
            vault_path=tmp,
            inbox_root="Inbox",
            topics_root="Topics",
            entities_root="Entities",
            auto_topic_whitelist=["AI", "股票", "GitHub"],
            auto_topic_blocklist=["测试", "路径"],
        ),
        telegram=TelegramConfig(result_bot_token="token"),
        summarizer=SummarizerConfig(api_base_url="https://example.com", api_key="k", model="m", timeout_seconds=30),
        extractors=ExtractorConfig(),
    )


class ExtractorTest(unittest.TestCase):
    def test_url_request_prefers_analyzer_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            document = StructuredDocument(
                title="Analyzer title",
                summary="Analyzer summary",
                sections=[SectionResult(heading="Intro", level=1, content="Analyzer body")],
                images=[],
                videos=[],
                tables=[],
            )
            with patch(
                "openclaw_capture_workflow.extractor.analyze_url",
                return_value=AnalysisOutcome(document=document, warnings=["sample warning"]),
            ):
                evidence = extractor.extract(
                    IngestRequest(
                        chat_id="-1",
                        reply_to_message_id="1",
                        request_id="job-analyzer-web",
                        source_kind="url",
                        source_url="https://example.com/article",
                        raw_text="https://example.com/article",
                        dry_run=False,
                    )
                )
            self.assertEqual(evidence.evidence_type, "structured_document")
            self.assertEqual(evidence.title, "Analyzer title")
            self.assertIn("Analyzer summary", evidence.text)
            self.assertEqual(evidence.metadata.get("structured_document", {}).get("title"), "Analyzer title")

    def test_video_request_prefers_analyzer_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            document = StructuredDocument(
                title="Video title",
                summary="Video summary",
                sections=[SectionResult(heading="Overview", level=1, content="Video body")],
                images=[],
                videos=[],
                tables=[],
            )
            with patch(
                "openclaw_capture_workflow.extractor.analyze_url",
                return_value=AnalysisOutcome(document=document, warnings=[]),
            ):
                evidence = extractor.extract(
                    IngestRequest(
                        chat_id="-1",
                        reply_to_message_id="1",
                        request_id="job-analyzer-video",
                        source_kind="video_url",
                        source_url="https://example.com/demo.mp4",
                        dry_run=False,
                    )
                )
            self.assertEqual(evidence.evidence_type, "structured_document")
            self.assertEqual(evidence.metadata.get("video_analysis_path"), "analyzer")

    def test_github_blob_from_url_parses_markdown_path(self) -> None:
        parsed = _github_blob_from_url("https://github.com/openai/openai-cookbook/blob/main/README.md")
        self.assertEqual(parsed, ("openai", "openai-cookbook", "main", "README.md"))

    def test_extract_wechat_article(self) -> None:
        html = """
        <html><head><title>ignored</title></head><body>
        <div id="js_content">
          <p>第一段正文内容，长度足够长，可以视为真正的文章内容。</p>
          <p>第二段正文内容，也应该被提取出来，而不是导航文字。</p>
        </div>
        </body></html>
        """
        text = _extract_wechat_article(html)
        self.assertIsNotNone(text)
        self.assertIn("第一段正文内容", text)
        self.assertIn("第二段正文内容", text)

    def test_parse_video_text_output_ignores_empty_structured_payload(self) -> None:
        text, meta = _parse_video_text_output('{"text":"","language":"zh","segments":[]}')
        self.assertEqual(text, "")
        self.assertEqual(meta.get("language"), "zh")

    def test_normalize_structured_ocr_output_prefers_text_field(self) -> None:
        parsed = _normalize_structured_ocr_output('{"text":"Hello OCR","language":"en"}')
        self.assertEqual(parsed, "Hello OCR")

    def test_normalize_structured_ocr_output_drops_empty_payload(self) -> None:
        parsed = _normalize_structured_ocr_output('{"text":"","segments":[]}')
        self.assertEqual(parsed, "")

    def test_extract_article_blocks_prefers_main_content(self) -> None:
        html = """
        <div class="header">首页 导航</div>
        <article>
          <h1>标题</h1>
          <p>这里是一篇较长的正文内容，包含真实的信息，而不是菜单项。</p>
          <p>第二段继续补充文章内容，用于验证正文块提取。</p>
        </article>
        """
        text = _extract_article_blocks(html)
        self.assertIsNotNone(text)
        self.assertIn("这里是一篇较长的正文内容", text)
        self.assertNotIn("首页 导航", text)

    def test_text_request_stays_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            evidence = extractor.extract(
                IngestRequest(
                    chat_id="-1",
                    reply_to_message_id="1",
                    request_id="job-1",
                    source_kind="pasted_text",
                    raw_text="直接粘贴的正文",
                )
            )
            self.assertEqual(evidence.evidence_type, "raw_text")
            self.assertEqual(evidence.text, "直接粘贴的正文")

    def test_text_request_extracts_signal_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            evidence = extractor.extract(
                IngestRequest(
                    chat_id="-1",
                    reply_to_message_id="1",
                    request_id="job-1b",
                    source_kind="pasted_text",
                    raw_text=(
                        "安装方法：/install-skill "
                        "https://github.com/Day1Global/Day1Global-Skills/raw/main/tech-earnings-deepdive.skill "
                        "项目地址：https://github.com/star23/Day1Global-Skills"
                    ),
                )
            )
            signals = evidence.metadata.get("signals", {})
            self.assertIn("https://github.com/star23/Day1Global-Skills", signals.get("links", []))
            self.assertIn("tech-earnings-deepdive", signals.get("skill_ids", []))

    def test_url_raw_text_same_as_url_does_not_block_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            extractor.config.extractors.webpage_text_command = "python3 -c 'import json; print(json.dumps({{\"title\":\"t\",\"text\":\"正文内容足够长，用来验证不会把 URL 本身误当成正文。正文继续补充一些文字。\"}}, ensure_ascii=False))'"
            evidence = extractor.extract(
                IngestRequest(
                    chat_id="-1",
                    reply_to_message_id="1",
                    request_id="job-2",
                    source_kind="url",
                    source_url="https://example.com/article",
                    raw_text="https://example.com/article",
                )
            )
            self.assertEqual(evidence.title, "t")
            self.assertIn("正文内容足够长", evidence.text)

    def test_legal_footer_detected(self) -> None:
        footer = "沪ICP备13030189号 | 营业执照 | 增值电信业务经营许可证：沪B2-20150021 | 互联网药品信息服务资格证书 | 违法不良信息举报电话：4006676810"
        self.assertTrue(_looks_like_legal_footer(footer))

    def test_extract_meta_description(self) -> None:
        html = '<meta name="description" content="最近在X上看到有人分享的一个美股财报分析 Skill，很适合对美股财报进行初步分析。">'
        self.assertIn("美股财报分析", _extract_meta_description(html))

    def test_extract_text_from_browser_snapshot_prefers_real_body(self) -> None:
        snapshot = """
        - generic [ref=e60]:
          - paragraph [ref=e61]:
            - link "沪ICP备13030189号" [ref=e62]
            - /url: https://creator.xiaohongshu.com/publish/publish?source=official
        - generic [ref=e174]: 推荐一个「美股财报深度分析」Skill
        - generic [ref=e176]:
          - text: 最近在X上看到有人分享的一个美股财报分析 Skill 我安装在OpenClaw里试用了一下 确实挺实用
          - /url: https://github.com/VoltAgent/awesome-openclaw-skills
          - link "#openclaw" [ref=e177]
        - generic [ref=e209]: 你这个open claw 是手机app吗？
        """
        text = _extract_text_from_browser_snapshot(snapshot)
        self.assertIn("美股财报分析 Skill", text)
        self.assertNotIn("沪ICP备13030189号", text)
        self.assertIn("github.com/VoltAgent/awesome-openclaw-skills", text)
        self.assertNotIn("creator.xiaohongshu.com", text)

    def test_extract_bilibili_viewer_feedback_from_snapshot_filters_questions_and_owner_replies(self) -> None:
        snapshot = """
        - heading "评论" [level=2]
        - generic [ref=e271]:
          - generic [ref=e277]:
            - generic [ref=e280]:
              - link "Mmming1011" [ref=e282] [cursor=pointer]
            - paragraph [ref=e292]:
              - text: 你
              - link "2月28日" [ref=e293] [cursor=pointer]
              - text: 强烈建议买入的
              - link "中科曙光" [ref=e295] [cursor=pointer]
              - text: ，到昨天才回本
        - generic [ref=e314]:
          - generic [ref=e319]:
            - generic [ref=e322]:
              - link "圆圆小缘_" [ref=e324] [cursor=pointer]
            - paragraph [ref=e334]:
              - text: 可以让
              - link "openclaw" [ref=e335] [cursor=pointer]
              - text: 实现美股市场的选股及自动化交易套利吗
        - generic [ref=e360]:
          - generic [ref=e361]:
            - generic [ref=e363]:
              - link "以太与弦Justice" [ref=e367] [cursor=pointer]
            - paragraph [ref=e373]: 6 那你得上最猛的模型最猛的信源 要钱的
        - generic [ref=e391]:
          - generic [ref=e396]:
            - generic [ref=e399]:
              - link "路人甲" [ref=e401] [cursor=pointer]
            - generic [ref=e411]:
              - paragraph [ref=e413]: 3月3号的 还挺准 让卖的都大跌
        """
        feedback = _extract_bilibili_viewer_feedback_from_snapshot(snapshot, owner_name="以太与弦Justice")
        self.assertEqual(
            feedback,
            [
                "你2月28日强烈建议买入的中科曙光，到昨天才回本",
                "3月3号的 还挺准 让卖的都大跌",
            ],
        )

    def test_extract_skill_signals_prefers_high_value_links(self) -> None:
        text = """
        推荐一个「美股财报深度分析」Skill
        最近在X上看到有人分享，直接把github链接丢给OpenClaw就行：
        https://github.com/VoltAgent/awesome-openclaw-skills
        https://creator.xiaohongshu.com/publish/publish?source=official
        tech-earnings-deepdive
        #openclaw #skill #美股
        """
        signals = _extract_skill_signals(text, "https://www.xiaohongshu.com/explore/abc")
        self.assertIn("美股财报深度分析Skill", [item.replace(" ", "") for item in signals.get("skills", [])])
        self.assertIn("https://github.com/VoltAgent/awesome-openclaw-skills", signals.get("links", []))
        self.assertNotIn("https://creator.xiaohongshu.com/publish/publish?source=official", signals.get("links", []))
        self.assertIn("tech-earnings-deepdive", signals.get("skill_ids", []))

    def test_extract_skill_signals_splits_adjacent_chinese_punctuation_urls(self) -> None:
        text = (
            "安装命令 /install-skill "
            "https://github.com/Day1Global/Day1Global-Skills/raw/main/tech-earnings-deepdive.skill。"
            "项目地址：https://github.com/star23/Day1Global-Skills。"
        )
        signals = _extract_skill_signals(text)
        links = signals.get("links", [])
        self.assertIn(
            "https://github.com/Day1Global/Day1Global-Skills/raw/main/tech-earnings-deepdive.skill",
            links,
        )
        self.assertIn("https://github.com/star23/Day1Global-Skills", links)
        self.assertFalse(any("项目地址" in link for link in links))

    def test_extract_skill_signals_normalizes_xhs_tracking_url(self) -> None:
        text = "来源链接：https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7?xsec_token=abc&xsec_source=pc_feed"
        signals = _extract_skill_signals(text, "https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7?xsec_token=abc&xsec_source=pc_feed")
        self.assertIn("https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7", signals.get("links", []))
        self.assertFalse(any("xsec_token=" in item for item in signals.get("links", [])))

    def test_split_user_guidance_from_video_evidence(self) -> None:
        evidence_text, guidance = _split_user_guidance_from_evidence(
            "重点看视频里提到的项目和部署方式。",
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1tyNNzxEpK",
        )
        self.assertEqual(evidence_text, "")
        self.assertIn("重点看", guidance)

    def test_extract_skill_signals_filters_noisy_skill_names(self) -> None:
        text = (
            "推荐一个美股财报深度分析 Skill\n"
            "/install-skill https://github.com/Day1Global/Day1Global-Skills/raw/main/tech-earnings-deepdive.skill\n"
            "Day1Global-Skill\n"
        )
        signals = _extract_skill_signals(text)
        skills = [item.lower() for item in signals.get("skills", [])]
        self.assertTrue(any("美股财报深度分析" in item for item in signals.get("skills", [])))
        self.assertFalse(any(item.startswith("install") for item in skills))

    def test_extract_skill_signals_infers_repo_from_ocr_owner_repo_line(self) -> None:
        text = """
        github.com
        star23/ Day1Global-Skills Public
        推荐一个「美股财报深度分析」Skill
        """
        signals = _extract_skill_signals(text, "https://www.xiaohongshu.com/explore/abc")
        self.assertIn("star23/Day1Global-Skills", signals.get("projects", []))
        self.assertIn("https://github.com/star23/Day1Global-Skills", signals.get("links", []))

    def test_extract_skill_signals_ignores_timestamp_owner_repo_false_positive(self) -> None:
        text = """
        github.com
        00:00 / 01:43
        本视频讲解 OpenClaw 的使用流程
        """
        signals = _extract_skill_signals(text, "https://www.bilibili.com/video/BV1HpP5zBEEp")
        self.assertNotIn("00/01", signals.get("projects", []))
        self.assertNotIn("https://github.com/00/01", signals.get("links", []))

    def test_extract_skill_signals_ignores_non_repo_owner_repo_phrases(self) -> None:
        text = """
        Day1Global/Day1Global-Skills
        Baillie Gifford/ARK
        https://github.com/star23/Day1Global-Skills
        """
        signals = _extract_skill_signals(text, "https://github.com/star23/Day1Global-Skills")
        self.assertIn("star23/Day1Global-Skills", signals.get("projects", []))
        self.assertNotIn("Gifford/ARK", signals.get("projects", []))
        self.assertNotIn("github.com/star23", signals.get("projects", []))

    def test_extract_skill_signals_skill_id_ignores_generic_slug_from_sentence(self) -> None:
        text = """
        This Skill automatically generates an institutional-grade deep analysis report.
        skill id: tech-earnings-deepdive
        """
        signals = _extract_skill_signals(text, "https://github.com/star23/Day1Global-Skills")
        self.assertIn("tech-earnings-deepdive", signals.get("skill_ids", []))
        self.assertNotIn("institutional-grade", signals.get("skill_ids", []))

    def test_extract_skill_signals_keeps_video_source_url_link(self) -> None:
        text = "视频介绍了全球热点监控和项目能力。"
        signals = _extract_skill_signals(text, "https://www.bilibili.com/video/BV1HpP5zBEEp")
        self.assertIn("https://www.bilibili.com/video/BV1HpP5zBEEp", signals.get("links", []))

    def test_extract_skill_signals_keeps_xiaohongshu_video_source_url_link(self) -> None:
        text = "这是一个小红书视频总结。"
        signals = _extract_skill_signals(text, "https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7")
        self.assertIn("https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7", signals.get("links", []))

    def test_github_blob_markdown_uses_raw_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            with patch(
                "openclaw_capture_workflow.extractor._fetch_text",
                return_value=(
                    "# Install Guide\n\n"
                    "Step 1: Install dependencies\n"
                    "Step 2: Run `openclaw`\n"
                    "/install-skill https://example.com/demo.skill\n"
                ),
            ):
                evidence = extractor.extract(
                    IngestRequest(
                        chat_id="-1",
                        reply_to_message_id="1",
                        request_id="github-blob-1",
                        source_kind="url",
                        source_url="https://github.com/openai/openai-cookbook/blob/main/docs/install.md",
                        raw_text="https://github.com/openai/openai-cookbook/blob/main/docs/install.md",
                    )
                )
            self.assertIn("文档路径: docs/install.md", evidence.text)
            self.assertIn("/install-skill https://example.com/demo.skill", evidence.text)
            self.assertEqual(evidence.metadata.get("source"), "github_blob")
            self.assertIn(
                "https://github.com/openai/openai-cookbook/blob/main/docs/install.md",
                evidence.metadata.get("signals", {}).get("links", []),
            )

    def test_github_blob_markdown_keeps_kubelet_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            with patch(
                "openclaw_capture_workflow.extractor._fetch_text",
                return_value=(
                    "# Container runtimes\n\n"
                    "Both the kubelet and the container runtime need compatible cgroup settings.\n"
                    "The container runtime endpoint must support CRI.\n"
                ),
            ):
                evidence = extractor.extract(
                    IngestRequest(
                        chat_id="-1",
                        reply_to_message_id="1",
                        request_id="github-blob-2",
                        source_kind="url",
                        source_url=(
                            "https://github.com/kubernetes/website/blob/main/content/en/docs/"
                            "setup/production-environment/container-runtimes.md"
                        ),
                        raw_text=(
                            "https://github.com/kubernetes/website/blob/main/content/en/docs/"
                            "setup/production-environment/container-runtimes.md"
                        ),
                    )
                )
            self.assertIn("kubelet", evidence.text.lower())
            self.assertIn("container runtime", evidence.text.lower())

    def test_extract_high_value_ocr_lines_filters_ui_noise(self) -> None:
        ocr_text = """
        小红书
        发现
        推荐一个「美股财报深度分析」Skill
        直接把github链接丢给OpenClaw
        https://github.com/VoltAgent/awesome-openclaw-skills
        你这个open claw 是手机app吗？
        """
        lines = _extract_high_value_ocr_lines(ocr_text)
        self.assertTrue(any("Skill" in line for line in lines))
        self.assertTrue(any("github" in line.lower() for line in lines))
        self.assertFalse(any("你这个open claw" in line for line in lines))
        self.assertFalse(any(line == "发现" for line in lines))

    def test_looks_like_command_line_ignores_long_prose_with_pipe(self) -> None:
        prose = "Install OpenClaw and bring up the | Guided setup with opencLaw"
        self.assertFalse(_looks_like_command_line(prose))
        self.assertFalse(_looks_like_command_line("SOPENCLAW"))
        self.assertTrue(_looks_like_command_line("curl -fsSL https://example.com/install.sh | bash"))

    def test_should_try_browser_ocr_skips_reliable_docs_domains(self) -> None:
        cfg = _config("/tmp")
        self.assertFalse(_should_try_browser_ocr("url", "https://docs.openclaw.ai/", "", cfg))
        self.assertFalse(_should_try_browser_ocr("url", "https://github.com/openclawai/openclaw", "", cfg))

    def test_canonicalize_video_source_url_for_bilibili_share_link(self) -> None:
        url = "https://www.bilibili.com/video/BV1HpP5zBEEp?share_source=weixin&timestamp=1772551573"
        normalized = _canonicalize_video_source_url(url)
        self.assertEqual(normalized, "https://www.bilibili.com/video/BV1HpP5zBEEp")

    def test_canonicalize_video_source_url_resolves_b23_short_link(self) -> None:
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def geturl(self):
                return "https://www.bilibili.com/video/BV19VAUzUEL6/?share_source=copy"

        with patch("openclaw_capture_workflow.extractor.urlrequest.urlopen", return_value=_Resp()):
            normalized = _canonicalize_video_source_url("https://b23.tv/fKZ8dA4")
        self.assertEqual(normalized, "https://www.bilibili.com/video/BV19VAUzUEL6")

    def test_canonicalize_video_source_url_normalizes_xiaohongshu_share_to_note(self) -> None:
        url = (
            "https://xiaohongshu.com/explore?app_platform=ios&type=video&target_note_id=69aea021000000001a028a59"
            "&xsec_token=abc"
        )
        normalized = _canonicalize_video_source_url(url)
        self.assertEqual(normalized, "https://www.xiaohongshu.com/explore/69aea021000000001a028a59")

    def test_sanitize_video_page_snapshot_text_filters_timeline_noise(self) -> None:
        text = """
        2026-03-02 14:15:01
        00:02 / 00:47
        平民版彭博终端来了！我用开源situation monitor把全球新闻+金融+地缘热点一屏看完 | Trae一键部署+实测 | Github项目
        我怎么觉得十几年前就用个这个 psv
        相关推荐
        """
        cleaned = _sanitize_video_page_snapshot_text(text, "平民版彭博终端来了！_哔哩哔哩_bilibili")
        self.assertIn("平民版彭博终端来了！", cleaned)
        self.assertNotIn("00:02 / 00:47", cleaned)
        self.assertNotIn("我怎么觉得十几年前就用个这个 psv", cleaned)
        self.assertNotIn("相关推荐", cleaned)

    def test_find_browser_tab_for_url_matches_xiaohongshu_share_variants(self) -> None:
        tabs = [
            {
                "targetId": "1",
                "url": "https://www.xiaohongshu.com/explore/69a3032400000000150305bb?app_platform=ios&apptime=111&share_id=aaa",
                "title": "推荐一个「美股财报深度分析」Skill - 小红书",
            }
        ]
        matched = _find_browser_tab_for_url(
            "https://www.xiaohongshu.com/explore/69a3032400000000150305bb?app_platform=ios&apptime=222&share_id=bbb",
            tabs,
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched["targetId"], "1")

    def test_find_or_open_browser_tab_with_state_marks_new_tab(self) -> None:
        with patch(
            "openclaw_capture_workflow.extractor._run_openclaw_browser_json",
            side_effect=[
                {"tabs": []},
                {"ok": True},
                {"tabs": [{"targetId": "t1", "url": "https://example.com/video", "title": "video"}]},
            ],
        ):
            tab, opened = _find_or_open_browser_tab_with_state("https://example.com/video", retries=1, delay_seconds=0)
        self.assertTrue(opened)
        self.assertEqual(tab["targetId"], "t1")

    def test_cleanup_browser_tab_pauses_and_closes_new_tab(self) -> None:
        calls: list[tuple] = []

        def _fake_browser_json(*args):
            calls.append(args)
            return {"ok": True}

        with patch("openclaw_capture_workflow.extractor._run_openclaw_browser_json", side_effect=_fake_browser_json):
            _cleanup_browser_tab({"targetId": "t-video-1"}, opened_for_capture=True)
        self.assertEqual(calls[0][0], "evaluate")
        self.assertEqual(calls[1], ("close", "t-video-1"))

    def test_extract_text_from_tencent_snapshot_keeps_steps(self) -> None:
        snapshot = """
        - heading "【保姆级教程】手把手教你安装OpenClaw并接入飞书，让AI在聊天软件里帮你干活" [level=1]
        - paragraph: 根据您的要求，我已将文章内容中的所有代码块设置好对应的代码语言。以下是修改后的完整文章内容：
        - heading "二、安装nodejs" [level=2]
        - paragraph: 官方下载地址：https://nodejs.org/zh-cn/download
        - heading "三、开始安装" [level=2]
        - heading "一）设置 PowerShell 执行权限" [level=4]
        - paragraph: 以管理员身份运行 PowerShell：
        - code: Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
        - code: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
        - heading "热门产品" [level=2]
        """
        text = _extract_text_from_tencent_snapshot(snapshot)
        self.assertIn("二、安装nodejs", text)
        self.assertIn("一）设置 PowerShell 执行权限", text)
        self.assertIn("命令：Set-ExecutionPolicy RemoteSigned -Scope CurrentUser", text)
        self.assertNotIn("热门产品", text)
        steps = _extract_steps_from_tencent_snapshot(snapshot)
        self.assertTrue(any("二、安装nodejs" in (step.get("title") or "") for step in steps))
        self.assertTrue(any("一）设置 PowerShell 执行权限" in (step.get("title") or "") for step in steps))
        self.assertTrue(any("Set-ExecutionPolicy" in (step.get("command") or "") for step in steps))

    def test_mixed_request_merges_web_text_raw_text_and_image_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            extractor.config.extractors.webpage_text_command = (
                "python3 -c 'import json; print(json.dumps({{\"title\":\"网页标题\",\"text\":\"网页正文包含 OpenClaw Skill 链接和安装说明。\"}}, ensure_ascii=False))'"
            )
            extractor.config.extractors.image_ocr_command = (
                "python3 -c 'print(\"tech-earnings-deepdive\\n推荐一个美股财报深度分析 Skill\\nhttps://github.com/VoltAgent/awesome-openclaw-skills\")'"
            )
            with patch(
                "openclaw_capture_workflow.extractor._fetch_openclaw_browser_screenshot_ocr",
                return_value=("", None),
            ):
                evidence = extractor.extract(
                    IngestRequest(
                        chat_id="-1",
                        reply_to_message_id="1",
                        request_id="mixed-1",
                        source_kind="mixed",
                        source_url="https://example.com/post",
                        raw_text="用户补充：这个 Skill 的安装方式更简单。",
                        image_refs=["/tmp/user-image-1.png"],
                    )
                )
            self.assertEqual(evidence.title, "网页标题")
            self.assertIn("用户补充", evidence.text)
            self.assertIn("网页正文包含", evidence.text)
            self.assertIn("[上传图片OCR]", evidence.text)
            self.assertIn("tech-earnings-deepdive", evidence.text)
            self.assertIn("/tmp/user-image-1.png", evidence.metadata.get("image_refs", []))
            signals = evidence.metadata.get("signals", {})
            self.assertIn("tech-earnings-deepdive", signals.get("skill_ids", []))

    def test_video_request_merges_subtitle_keyframes_ocr_and_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            extractor.config.extractors.video_subtitle_command = (
                "python3 -c 'print(\"本视频介绍如何安装 OpenClaw Skill。\")'"
            )
            extractor.config.extractors.video_keyframes_command = (
                "python3 -c 'print(\"frame-001.jpg\\n关键帧提示：查看 README 安装步骤\")'"
            )
            extractor.config.extractors.image_ocr_command = (
                "python3 -c 'print(\"命令：openclaw skill add awesome-openclaw-skills\\nhttps://github.com/VoltAgent/awesome-openclaw-skills\")'"
            )
            evidence = extractor.extract(
                IngestRequest(
                    chat_id="-1",
                    reply_to_message_id="1",
                    request_id="video-1",
                    source_kind="video_url",
                    source_url="https://example.com/video/abc",
                )
            )
            self.assertEqual(evidence.evidence_type, "multimodal_video")
            self.assertIn("[关键帧OCR]", evidence.text)
            self.assertIn("openclaw skill add awesome-openclaw-skills", evidence.text)
            self.assertTrue(any(item.endswith("frame-001.jpg") for item in evidence.keyframes))
            self.assertIn("temp_image_refs", evidence.metadata)
            self.assertIn("signals", evidence.metadata)
            signals = evidence.metadata["signals"]
            self.assertIn("https://github.com/VoltAgent/awesome-openclaw-skills", signals.get("links", []))

    def test_video_skips_audio_when_subtitle_sufficient_and_parses_duration_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            extractor.config.extractors.video_subtitle_command = (
                "python3 -c 'import json; print(json.dumps({{\"duration_seconds\": 601, \"language\": \"zh\", \"text\": \"本视频详细讲解OpenClaw部署。\" * 40}}, ensure_ascii=False))'"
            )
            extractor.config.extractors.video_audio_command = (
                "python3 -c 'raise SystemExit(\"audio should not run\")'"
            )
            evidence = extractor.extract(
                IngestRequest(
                    chat_id="-1",
                    reply_to_message_id="1",
                    request_id="video-2",
                    source_kind="video_url",
                    source_url="https://example.com/video/xyz",
                )
            )
            self.assertEqual(evidence.evidence_type, "multimodal_video")
            self.assertIn("video_duration_seconds", evidence.metadata)
            self.assertEqual(int(evidence.metadata["video_duration_seconds"]), 601)
            self.assertEqual(evidence.metadata.get("subtitle_language"), "zh")
            self.assertEqual(evidence.metadata.get("audio_skipped_reason"), "subtitle_sufficient")
            tracks = evidence.metadata.get("tracks", {})
            self.assertTrue(tracks.get("has_subtitle"))
            self.assertFalse(tracks.get("has_transcript"))

    def test_skill_signal_extraction_does_not_treat_readme_prose_as_commands(self) -> None:
        text = (
            "项目仓库: VoltAgent/awesome-openclaw-skills\n"
            "仓库地址: https://github.com/VoltAgent/awesome-openclaw-skills\n"
            "<strong>Discover 5490+ community-built OpenClaw skills, organized by category.\n"
            "OpenClaw is a locally-running AI assistant that operates directly on your machine.\n"
            "npm install -g openclaw@latest\n"
        )
        signals = _extract_skill_signals(text, "https://github.com/VoltAgent/awesome-openclaw-skills")
        self.assertIn("https://github.com/VoltAgent/awesome-openclaw-skills", signals.get("links", []))
        self.assertIn("npm install -g openclaw@latest", signals.get("commands", []))
        self.assertFalse(any("Discover 5490" in item for item in signals.get("commands", [])))
        self.assertFalse(any("locally-running AI assistant" in item for item in signals.get("commands", [])))

    def test_video_omits_low_signal_keyframe_ocr_when_transcript_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            extractor.config.extractors.video_audio_command = (
                "python3 -c 'import json; print(json.dumps({{\"text\":\"本视频完整讲解全球监控系统的核心功能与部署方式。\" * 20, \"duration_seconds\": 47}}, ensure_ascii=False))'"
            )
            extractor.config.extractors.video_keyframes_command = (
                "python3 -c 'print(\"frame-001.jpg\\nframe-002.jpg\")'"
            )
            extractor.config.extractors.image_ocr_command = (
                "python3 -c 'print(\"鳳踪，焦成于使一的态势感知界路。\")'"
            )
            evidence = extractor.extract(
                IngestRequest(
                    chat_id="-1",
                    reply_to_message_id="1",
                    request_id="video-ocr-suppress-1",
                    source_kind="video_url",
                    source_url="https://example.com/video/ocr-suppress",
                )
            )
            self.assertEqual(evidence.evidence_type, "multimodal_video")
            self.assertNotIn("[关键帧OCR]", evidence.text)
            tracks = evidence.metadata.get("tracks", {})
            self.assertTrue(tracks.get("has_transcript"))

    def test_video_dry_run_probe_skips_audio_and_keyframes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            extractor.config.extractors.video_subtitle_command = (
                "python3 -c 'print(\"本视频前90秒用于探针验证，包含关键术语与流程概览。\" * 20)'"
            )
            extractor.config.extractors.video_audio_command = (
                "python3 -c 'raise SystemExit(\"audio should be skipped in dry_run probe\")'"
            )
            extractor.config.extractors.video_keyframes_command = (
                "python3 -c 'raise SystemExit(\"keyframes should be skipped in dry_run probe\")'"
            )
            evidence = extractor.extract(
                IngestRequest(
                    chat_id="-1",
                    reply_to_message_id="1",
                    request_id="video-probe-1",
                    source_kind="video_url",
                    source_url="https://example.com/video/probe",
                    dry_run=True,
                )
            )
            self.assertEqual(evidence.evidence_type, "multimodal_video")
            self.assertEqual(evidence.metadata.get("video_probe_seconds"), 90)
            self.assertEqual(evidence.metadata.get("video_extraction_profile"), "dry_run_probe")
            self.assertEqual(evidence.metadata.get("audio_skipped_reason"), "dry_run_skip_video_audio")
            self.assertEqual(evidence.metadata.get("keyframes_skipped_reason"), "dry_run_skip_video_keyframes")
            self.assertNotIn("fetch_warnings", evidence.metadata)
            tracks = evidence.metadata.get("tracks", {})
            self.assertTrue(tracks.get("has_subtitle"))
            self.assertFalse(tracks.get("has_transcript"))
            self.assertFalse(tracks.get("has_keyframes"))

    def test_video_dry_run_bilibili_uses_audio_when_subtitles_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            extractor.config.extractors.video_subtitle_command = (
                "python3 -c 'import json; print(json.dumps({{\"text\":\"\",\"segments\":[]}}, ensure_ascii=False))'"
            )
            audio_payload = json.dumps(
                {
                    "text": "这是一段真实音频转写内容，用于验证 dry_run 质量优先模式。",
                    "duration_seconds": 31,
                    "segments": [
                        {"start": 2.0, "end": 5.0, "text": "第一段关键结论"},
                        {"start": 8.0, "end": 12.0, "text": "第二段关键操作"},
                    ],
                },
                ensure_ascii=False,
            )
            extractor.config.extractors.video_audio_command = (
                "python3 -c 'print(" + json.dumps(audio_payload, ensure_ascii=False).replace("{", "{{").replace("}", "}}") + ")'"
            )
            evidence = extractor.extract(
                IngestRequest(
                    chat_id="-1",
                    reply_to_message_id="1",
                    request_id="video-bili-dry-audio-1",
                    source_kind="video_url",
                    source_url="https://www.bilibili.com/video/BV1HpP5zBEEp",
                    dry_run=True,
                )
            )
            self.assertEqual(evidence.evidence_type, "multimodal_video")
            self.assertEqual(evidence.metadata.get("audio_probe_reason"), "dry_run_quality_upgrade_missing_subtitles")
            self.assertNotIn("audio_skipped_reason", evidence.metadata)
            self.assertIn("video_audio_asr", evidence.metadata.get("evidence_sources", []))
            self.assertIn("timeline_highlights", evidence.metadata)
            self.assertTrue(any("第一段关键结论" in item for item in evidence.metadata.get("timeline_highlights", [])))
            self.assertIn("[视频时间线要点]", evidence.text)
            self.assertIn("真实音频转写内容", evidence.text)

    def test_bilibili_platform_duration_overrides_probe_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            extractor.config.extractors.video_subtitle_command = (
                "python3 -c 'import json; print(json.dumps({{\"text\":\"\",\"segments\":[]}}, ensure_ascii=False))'"
            )
            audio_payload = json.dumps(
                {
                    "text": "这是一段真实音频转写内容，用于验证总时长保留。",
                    "duration_seconds": 31,
                    "segments": [
                        {"start": 2.0, "end": 5.0, "text": "第一段关键结论"},
                    ],
                },
                ensure_ascii=False,
            )
            extractor.config.extractors.video_audio_command = (
                "python3 -c 'print(" + json.dumps(audio_payload, ensure_ascii=False).replace("{", "{{").replace("}", "}}") + ")'"
            )
            with patch(
                "openclaw_capture_workflow.extractor._fetch_bilibili_video_metadata",
                return_value=(
                    "测试长视频",
                    "[视频元数据]\n标题: 测试长视频\n视频总时长: 43分17秒",
                    {"bilibili_duration_seconds": 2597},
                ),
            ):
                evidence = extractor.extract(
                    IngestRequest(
                        chat_id="-1",
                        reply_to_message_id="1",
                        request_id="video-bili-duration-1",
                        source_kind="video_url",
                        source_url="https://www.bilibili.com/video/BV1HpP5zBEEp",
                        dry_run=True,
                    )
                )
            self.assertEqual(int(evidence.metadata.get("video_duration_seconds", 0)), 2597)
            self.assertEqual(evidence.metadata.get("bilibili_duration_seconds"), 2597)

    def test_video_dry_run_xhs_uses_keyframes_when_subtitles_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            extractor.config.extractors.video_subtitle_command = (
                "python3 -c 'import json; print(json.dumps({{\"text\":\"\",\"segments\":[]}}, ensure_ascii=False))'"
            )
            extractor.config.extractors.video_keyframes_command = (
                "python3 -c 'print(\"frame-001.jpg\\nframe-002.jpg\")'"
            )
            extractor.config.extractors.image_ocr_command = (
                "python3 -c 'print(\"公司要你的流水目的只有一个压价\\n好offer我就写1万\")'"
            )
            evidence = extractor.extract(
                IngestRequest(
                    chat_id="-1",
                    reply_to_message_id="1",
                    request_id="video-xhs-dry-keyframes-1",
                    source_kind="video_url",
                    source_url="https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7",
                    dry_run=True,
                )
            )
            self.assertEqual(evidence.metadata.get("keyframes_probe_reason"), "dry_run_quality_upgrade_missing_subtitles")
            self.assertIn("video_keyframe_ocr", evidence.metadata.get("evidence_sources", []))
            self.assertIn("[关键帧OCR]", evidence.text)

    def test_video_without_tracks_falls_back_to_page_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            with patch(
                "openclaw_capture_workflow.extractor._fetch_bilibili_video_metadata",
                return_value=(None, "", {}),
            ), patch(
                "openclaw_capture_workflow.extractor._fetch_openclaw_browser_snapshot",
                return_value=("测试视频标题", "这是视频页面正文补充，包含项目名和核心观点。"),
            ) as mocked_snapshot:
                evidence = extractor.extract(
                    IngestRequest(
                        chat_id="-1",
                        reply_to_message_id="1",
                        request_id="video-page-fallback-1",
                        source_kind="video_url",
                        source_url="https://www.bilibili.com/video/BV1HpP5zBEEp?share_source=weixin&timestamp=1772551573",
                        dry_run=True,
                    )
                )
            self.assertEqual(mocked_snapshot.call_args.args[0], "https://www.bilibili.com/video/BV1HpP5zBEEp")
            self.assertEqual(evidence.evidence_type, "multimodal_video")
            self.assertIn("[视频页面补充]", evidence.text)
            self.assertIn("包含项目名和核心观点", evidence.text)
            self.assertEqual(evidence.metadata.get("page_title"), "测试视频标题")
            self.assertTrue(evidence.metadata.get("video_page_snapshot_used"))
            self.assertEqual(evidence.source_url, "https://www.bilibili.com/video/BV1HpP5zBEEp")
            tracks = evidence.metadata.get("tracks", {})
            self.assertFalse(tracks.get("has_subtitle"))
            self.assertFalse(tracks.get("has_transcript"))
            self.assertEqual(evidence.metadata.get("video_story_blocks"), [])

    def test_xiaohongshu_url_generates_blocked_notice_when_page_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            with patch(
                "openclaw_capture_workflow.extractor._fetch_openclaw_browser_snapshot",
                side_effect=RuntimeError("browser tab not found for url"),
            ), patch(
                "openclaw_capture_workflow.extractor._fetch_html_document",
                return_value=("小红书 - 你访问的页面不见了", "沪ICP备13030189号 | 营业执照 | 违法不良信息举报电话"),
            ), patch(
                "openclaw_capture_workflow.extractor._fetch_openclaw_browser_screenshot_ocr",
                side_effect=RuntimeError("browser tab not found for screenshot OCR"),
            ):
                evidence = extractor.extract(
                    IngestRequest(
                        chat_id="-1",
                        reply_to_message_id="1",
                        request_id="xhs-web-blocked-1",
                        source_kind="url",
                        source_url="https://www.xiaohongshu.com/explore/69a3032400000000150305bb",
                    )
                )
            self.assertEqual(evidence.coverage, "partial")
            self.assertIn("小红书图文当前在本地环境下已经返回", evidence.text)
            self.assertIn("web_blocked_notice", evidence.metadata.get("evidence_sources", []))

    def test_video_extract_adds_viewer_feedback_and_story_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            extractor.config.extractors.video_subtitle_command = (
                "python3 -c 'import json; print(json.dumps({{\"text\":\"\",\"segments\":[]}}, ensure_ascii=False))'"
            )
            audio_payload = json.dumps(
                {
                    "text": (
                        "今天给大家介绍一个用龙虾做的一件事情。"
                        "最后会在每天早上开盘之前给你一个当天自选股的分析。"
                        "会给出买入或者持有的建议。"
                    ),
                    "duration_seconds": 31,
                    "segments": [
                        {"start": 2.0, "end": 5.0, "text": "把自选股交给OpenClaw"},
                        {"start": 8.0, "end": 12.0, "text": "开盘前给出买入或持有建议"},
                    ],
                },
                ensure_ascii=False,
            )
            extractor.config.extractors.video_audio_command = (
                "python3 -c 'print(" + json.dumps(audio_payload, ensure_ascii=False).replace("{", "{{").replace("}", "}}") + ")'"
            )
            with patch(
                "openclaw_capture_workflow.extractor._fetch_bilibili_video_metadata",
                return_value=(
                    "OpenClaw你虾哥每天股票量化交易推荐",
                    "[视频元数据]\n标题: OpenClaw你虾哥每天股票量化交易推荐\n简介: 图一乐 别真跟着买 当然我跟着买了",
                    {"bilibili_owner": "以太与弦Justice", "bilibili_description": "图一乐 别真跟着买 当然我跟着买了"},
                ),
            ), patch(
                "openclaw_capture_workflow.extractor._fetch_bilibili_viewer_feedback",
                return_value=(
                    ["评论区有人反馈信号有一定参考性", "也有人提醒不要直接跟单"],
                    {"attempted": True, "count": 2, "warning": None},
                ),
            ):
                evidence = extractor.extract(
                    IngestRequest(
                        chat_id="-1",
                        reply_to_message_id="1",
                        request_id="video-story-block-1",
                        source_kind="video_url",
                        source_url="https://www.bilibili.com/video/BV1bFPMzFEnd",
                        dry_run=True,
                    )
                )
            self.assertEqual(
                evidence.metadata.get("viewer_feedback"),
                ["评论区有人反馈信号有一定参考性", "也有人提醒不要直接跟单"],
            )
            self.assertEqual(evidence.metadata.get("viewer_feedback_capture", {}).get("count"), 2)
            story_blocks = evidence.metadata.get("video_story_blocks", [])
            labels = [item.get("label") for item in story_blocks]
            self.assertIn("core_topic", labels)
            self.assertIn("workflow", labels)
            self.assertIn("risk", labels)

    def test_video_viewer_feedback_failure_does_not_break_extract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            extractor.config.extractors.video_subtitle_command = (
                "python3 -c 'print(\"[00:03] 视频展示OpenClaw的分析流程\")'"
            )
            with patch(
                "openclaw_capture_workflow.extractor._fetch_bilibili_video_metadata",
                return_value=("测试视频", "[视频元数据]\n标题: 测试视频", {"bilibili_owner": "测试UP"}),
            ), patch(
                "openclaw_capture_workflow.extractor._fetch_bilibili_viewer_feedback",
                return_value=([], {"attempted": True, "count": 0, "warning": "browser failed"}),
            ):
                evidence = extractor.extract(
                    IngestRequest(
                        chat_id="-1",
                        reply_to_message_id="1",
                        request_id="video-feedback-fail-1",
                        source_kind="video_url",
                        source_url="https://www.bilibili.com/video/BV1bFPMzFEnd",
                        dry_run=True,
                    )
                )
            self.assertEqual(evidence.evidence_type, "multimodal_video")
            self.assertEqual(evidence.metadata.get("viewer_feedback"), [])
            self.assertEqual(evidence.metadata.get("viewer_feedback_capture", {}).get("warning"), "browser failed")

    def test_fetch_bilibili_video_metadata_formats_title_desc_and_tags(self) -> None:
        with patch(
            "openclaw_capture_workflow.extractor._fetch_json",
            side_effect=[
                {
                    "code": 0,
                    "data": {
                        "title": "家人们我说杂技伤害特别高有人信吗",
                        "desc": "谁不喜欢猎宝体操的时候毒脚丫连踹带上毒呢？",
                        "duration": 2597,
                        "owner": {"name": "测试UP"},
                        "stat": {"view": 123456, "like": 7890},
                    },
                },
                {
                    "code": 0,
                    "data": [
                        {"tag_name": "杀戮尖塔2"},
                        {"tag_name": "游戏杂谈"},
                    ],
                },
            ],
        ):
            title, text, meta = _fetch_bilibili_video_metadata("https://www.bilibili.com/video/BV1tyNNzxEpK")
        self.assertEqual(title, "家人们我说杂技伤害特别高有人信吗")
        self.assertIn("简介: 谁不喜欢猎宝体操的时候毒脚丫连踹带上毒呢？", text)
        self.assertIn("视频总时长: 43分17秒", text)
        self.assertIn("标签: 杀戮尖塔2 | 游戏杂谈", text)
        self.assertEqual(meta["bilibili_owner"], "测试UP")
        self.assertEqual(int(meta["bilibili_duration_seconds"]), 2597)

    def test_video_text_compaction_keeps_signal_and_caps_length(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = EvidenceExtractor(_config(tmp), Path(tmp) / "artifacts")
            extractor.config.video_accuracy.max_evidence_chars = 1200
            extractor.config.video_accuracy.max_evidence_lines = 80
            noise = "\n".join(["点赞 投币 收藏" for _ in range(180)])
            body = "\n".join([f"[{i:02d}:00] 这是教程说明第{i}段，包含环境配置与部署细节。" for i in range(1, 90)])
            signal = "安装命令：/install-skill https://github.com/star23/Day1Global-Skills"
            subtitle_text = noise + "\n" + body + "\n" + signal
            extractor.config.extractors.video_subtitle_command = (
                "python3 -c 'print(" + json.dumps(subtitle_text, ensure_ascii=False) + ")'"
            )
            evidence = extractor.extract(
                IngestRequest(
                    chat_id="-1",
                    reply_to_message_id="1",
                    request_id="video-compact-1",
                    source_kind="video_url",
                    source_url="https://example.com/video/compact",
                    dry_run=True,
                )
            )
            compact_meta = evidence.metadata.get("video_text_compacted", {})
            self.assertTrue(compact_meta)
            self.assertLessEqual(int(compact_meta.get("kept_chars", 99999)), 1200)
            self.assertIn("/install-skill", evidence.text)
            self.assertNotIn("点赞 投币 收藏", evidence.text)


if __name__ == "__main__":
    unittest.main()
