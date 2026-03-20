import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.config import AppConfig, ExtractorConfig, ObsidianConfig, SummarizerConfig, TelegramConfig
from openclaw_capture_workflow.models import EvidenceBundle, EvidenceItem, IngestRequest, SummaryResult
from openclaw_capture_workflow.processor import (
    WorkflowProcessor,
    _extract_steps_from_text,
    _has_sufficient_evidence_text,
    _normalize_source_url_for_cache,
    _summary_quality_score,
)
from openclaw_capture_workflow.storage import JobStore


class FakeNoteRenderer:
    def render(self, materials):
        return f"# {materials.get('title', '未命名内容')}\n\n{materials.get('summary', {}).get('conclusion', '')}\n"


def _attach_note_renderer(processor: WorkflowProcessor) -> WorkflowProcessor:
    processor.writer.renderer = FakeNoteRenderer()
    return processor


class WeakSummarizer:
    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
        return SummaryResult(
            title="不应到达",
            primary_topic="测试",
            secondary_topics=[],
            entities=[],
            conclusion="不应到达",
            bullets=[],
            evidence_quotes=[],
            coverage="partial",
            confidence="low",
            note_tags=[],
            follow_up_actions=[],
        )


class WeakEvidenceProcessor(WorkflowProcessor):
    pass


class StaticExtractor:
    def __init__(self, evidence: EvidenceBundle) -> None:
        self._evidence = evidence

    def extract(self, request: IngestRequest) -> EvidenceBundle:
        return self._evidence


class SequenceExtractor:
    def __init__(self, evidences: list[EvidenceBundle]) -> None:
        self._evidences = list(evidences)
        self._idx = 0

    def extract(self, request: IngestRequest) -> EvidenceBundle:
        if self._idx < len(self._evidences):
            item = self._evidences[self._idx]
            self._idx += 1
            return item
        return self._evidences[-1]


class StableSummarizer:
    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
        lead = "视频证据补救后得到更完整语音轨道。"
        return SummaryResult(
            title="视频补救验证",
            primary_topic="视频分析",
            secondary_topics=["OpenClaw"],
            entities=[],
            conclusion=lead,
            bullets=["包含语音轨道", "包含关键步骤", "可生成稳定摘要"],
            evidence_quotes=["语音轨道可用", "关键步骤已提取"],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=["复核结论", "对照原视频时间轴"],
        )


class FluffyWebSummarizer:
    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
        return SummaryResult(
            title="OpenClaw 安装指南",
            primary_topic="OpenClaw",
            secondary_topics=[],
            entities=[],
            conclusion="这是一个安装说明。",
            bullets=[
                "关键链接: https://docs.openclaw.ai/",
                "支持 WhatsApp、Telegram、Discord、iMessage 等平台。",
                "适合作为参考。",
            ],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )


class CountingVideoSummarizer:
    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
        return SummaryResult(
            title="面试中最常见的12个问题",
            primary_topic="求职面试常见问题及应对策略",
            secondary_topics=["自我介绍", "职业规划"],
            entities=[],
            conclusion="当前已覆盖 10/12 项，可先用于快速判断，但不能当作完整版本。",
            bullets=[
                "1. 请做自我介绍",
                "2. 你的业余爱好是什么",
                "3. 谈谈你的缺点",
                "4. 为什么想进入我们公司",
                "5. 给我一个录取你的理由",
                "6. 你的职业发展规划是什么",
                "7. 如何看待加班",
                "8. 如何处理工作压力",
                "9. 喜欢独立工作还是团队合作",
                "10. 你的期望薪资是多少",
            ],
            evidence_quotes=["问题一 请做自我介绍", "问题十 你的期望薪资是多少"],
            coverage="partial",
            confidence="medium",
            note_tags=[],
            follow_up_actions=["回到原视频确认剩余两项问题"],
            outcome="partial",
            uncertainties=["仅覆盖 10/12 项，不能当作完整枚举总结。"],
        )


class SilentNotifier:
    def send_result(self, ingest, summary, note_path, structure_map, open_url) -> None:
        return None

    def send_text(self, chat_id, text, reply_to_message_id=None) -> None:
        return None


def _long_interview_outline_lines() -> list[str]:
    return [
        "问题一 请做自我介绍，建议控制在三分钟内并紧扣岗位经验。",
        "问题二 你的业余爱好是什么，回答时要体现积极性和个人特点。",
        "问题三 谈谈你的缺点，重点是结合岗位要求说明改进方式。",
        "问题四 为什么想进入我们公司，要提前了解行业、公司和岗位。",
        "问题五 给我一个录取你的理由，需要回答岗位要求与个人优势的匹配。",
        "问题六 你的职业发展规划是什么，回答时不要空泛，要有明确步骤。",
        "问题七 如何看待加班，要体现责任感同时保持边界意识。",
        "问题八 如何处理工作压力，要举例说明具体的调节和拆解方式。",
        "问题九 喜欢独立工作还是团队合作，要说明协作中的角色和贡献。",
        "问题十 你的期望薪资是多少，要结合市场水平和岗位价值回答。",
    ]


class ProcessorValidationTest(unittest.TestCase):
    def test_plain_url_uses_web_capture_and_can_summarize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(
                listen_host="127.0.0.1",
                listen_port=8765,
                state_dir="state",
                obsidian=ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "股票"],
                    auto_topic_blocklist=["测试", "总结", "路径"],
                    auto_entity_pages=False,
                ),
                telegram=TelegramConfig(result_bot_token="token"),
                summarizer=SummarizerConfig(api_base_url="https://example.com", api_key="k", model="m", timeout_seconds=30),
                extractors=ExtractorConfig(),
            )
            state_dir = Path(tmp) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            jobs = JobStore(state_dir / "jobs")
            cfg.extractors.webpage_text_command = (
                "python3 -c 'import json; print(json.dumps({{\"title\":\"示例网页\",\"text\":\"这是一段足够长的网页正文，用于验证普通 URL 在 web capture 路径下可以形成可总结证据。这里继续补充一些说明，让文本长度明显超过门槛。\"}}, ensure_ascii=False))'"
            )
            processor = _attach_note_renderer(WorkflowProcessor(cfg, jobs, StableSummarizer(), state_dir))
            processor.start()
            ingest = IngestRequest(
                chat_id="-1001",
                reply_to_message_id="42",
                request_id="job-weak-1",
                source_kind="url",
                source_url="https://example.com/a",
                raw_text="https://example.com/a",
                dry_run=True,
            )
            processor.enqueue(ingest)
            processor._queue.join()
            job = jobs.load("job-weak-1")
            processor.stop()
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "done")
            self.assertEqual(job.result["outcome"], "summarized")
            self.assertIn("capture_manifest", job.result)
            self.assertIn("web_html", job.result["capture_manifest"])

    def test_xiaohongshu_blocked_notice_can_continue_to_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(
                listen_host="127.0.0.1",
                listen_port=8765,
                state_dir="state",
                obsidian=ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "股票"],
                    auto_topic_blocklist=["测试", "总结", "路径"],
                    auto_entity_pages=False,
                ),
                telegram=TelegramConfig(result_bot_token="token"),
                summarizer=SummarizerConfig(api_base_url="https://example.com", api_key="k", model="m", timeout_seconds=30),
                extractors=ExtractorConfig(),
            )
            state_dir = Path(tmp) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            jobs = JobStore(state_dir / "jobs")
            processor = _attach_note_renderer(WorkflowProcessor(cfg, jobs, StableSummarizer(), state_dir))
            processor.notifier = SilentNotifier()
            processor.extractor = StaticExtractor(
                EvidenceBundle(
                    source_kind="url",
                    source_url="https://www.xiaohongshu.com/explore/69a3032400000000150305bb",
                    platform_hint="xiaohongshu",
                    title="小红书 - 你访问的页面不见了",
                    text="这条小红书图文当前在本地环境下已经返回“页面不可见/暂时无法浏览”，所以拿不到正文内容。",
                    evidence_type="visible_page_text",
                    coverage="partial",
                    metadata={"evidence_sources": ["web_blocked_notice"]},
                )
            )
            processor.start()
            ingest = IngestRequest(
                chat_id="-1001",
                reply_to_message_id="42",
                request_id="job-xhs-blocked-web",
                source_kind="url",
                source_url="https://www.xiaohongshu.com/explore/69a3032400000000150305bb",
                raw_text="https://www.xiaohongshu.com/explore/69a3032400000000150305bb",
                dry_run=False,
            )
            processor.enqueue(ingest)
            processor._queue.join()
            job = jobs.load("job-xhs-blocked-web")
            processor.stop()
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "done")
            self.assertIn("note", job.result)
            self.assertEqual(job.result["summary_mode"], "web_blocked_notice")
            self.assertIn("当前页面不可见或正文未返回", job.result["summary"]["conclusion"])
            note_path = Path(tmp) / str(job.result["note"]["note_path"])
            content = note_path.read_text(encoding="utf-8")
            self.assertIn("当前页面不可见或正文未返回", content)

    def test_extract_steps_from_text_picks_numbered_steps(self) -> None:
        text = """
        一、安装nodejs
        官方下载地址：https://nodejs.org/zh-cn/download
        二、开始安装
        一）设置 PowerShell 执行权限
        命令：Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
        """
        steps = _extract_steps_from_text(text)
        self.assertIn("一、安装nodejs", steps)
        self.assertIn("二、开始安装", steps)
        self.assertIn("一）设置 PowerShell 执行权限", steps)
        self.assertIn("命令：Set-ExecutionPolicy RemoteSigned -Scope CurrentUser", steps)

    def test_video_result_keeps_shared_outline_count_across_gate_and_assessment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(
                listen_host="127.0.0.1",
                listen_port=8765,
                state_dir="state",
                obsidian=ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "股票"],
                    auto_topic_blocklist=["测试", "总结", "路径"],
                    auto_entity_pages=False,
                ),
                telegram=TelegramConfig(result_bot_token="token"),
                summarizer=SummarizerConfig(api_base_url="https://example.com", api_key="k", model="m", timeout_seconds=30),
                extractors=ExtractorConfig(),
            )
            state_dir = Path(tmp) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            jobs = JobStore(state_dir / "jobs")
            processor = _attach_note_renderer(WorkflowProcessor(cfg, jobs, StableSummarizer(), state_dir))
            processor.notifier = SilentNotifier()
            interview_lines = _long_interview_outline_lines()
            interview_corpus = "\n".join(interview_lines + interview_lines)
            processor.extractor = StaticExtractor(
                EvidenceBundle(
                    source_kind="video_url",
                    source_url="https://www.bilibili.com/video/BV1ggpcevEgk",
                    platform_hint="bilibili",
                    title="求职面试中，最常见的12个问题",
                    text=interview_corpus,
                    transcript=interview_corpus,
                    evidence_type="multimodal_video",
                    coverage="full",
                    metadata={
                        "video_duration_seconds": 453.0,
                        "tracks": {"has_transcript": True, "has_subtitle": False, "has_keyframes": False, "has_keyframe_ocr": False},
                        "evidence_sources": ["video_audio_asr"],
                    },
                    evidence_items=[
                        EvidenceItem(
                            modality="speech",
                            source="asr",
                            provider="video_audio_command",
                            text=interview_corpus,
                            timestamp_start=0.0,
                            timestamp_end=400.0,
                            confidence=0.9,
                            is_primary=True,
                        ),
                    ],
                )
            )
            processor.video_summarizer = CountingVideoSummarizer()
            processor.start()
            ingest = IngestRequest(
                chat_id="-1001",
                reply_to_message_id="42",
                request_id="job-video-outline-shared",
                source_kind="video_url",
                source_url="https://www.bilibili.com/video/BV1ggpcevEgk",
                raw_text="https://www.bilibili.com/video/BV1ggpcevEgk",
                dry_run=True,
            )
            processor.enqueue(ingest)
            processor._queue.join()
            job = jobs.load("job-video-outline-shared")
            processor.stop()
            self.assertIsNotNone(job)
            gate = job.result["quality_gate"]
            self.assertEqual(gate["retained_outline_count"], 10)
            self.assertIn("outline_partial:10/12", job.result["summary_quality"]["reasons"])
            self.assertIn("enumeration coverage incomplete (10/12)", job.result["video_assessment"]["reasons"])

    def test_evidence_gate_accepts_signal_rich_short_image_text(self) -> None:
        text = "推荐一个 Skill\n技能ID: tech-earnings-deepdive\n链接: https://github.com/VoltAgent/awesome-openclaw-skills"
        metadata = {
            "signals": {
                "skills": ["美股财报深度分析 Skill"],
                "skill_ids": ["tech-earnings-deepdive"],
                "links": ["https://github.com/VoltAgent/awesome-openclaw-skills"],
            }
        }
        self.assertTrue(_has_sufficient_evidence_text("image", text, None, metadata))

    def test_summary_quality_score_detects_missing_signal_coverage(self) -> None:
        evidence = EvidenceBundle(
            source_kind="url",
            source_url="https://example.com",
            platform_hint="web",
            title="t",
            text="正文",
            evidence_type="visible_page_text",
            coverage="full",
            metadata={
                "signals": {
                    "projects": ["star23/Day1Global-Skills"],
                    "skill_ids": ["tech-earnings-deepdive"],
                }
            },
        )
        summary = SummaryResult(
            title="测试",
            primary_topic="技能推荐",
            secondary_topics=[],
            entities=[],
            conclusion="已提取核心事实。",
            bullets=["一般描述", "无具体项目", "无具体ID"],
            evidence_quotes=[],
            coverage="full",
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )
        score, reasons, coverage = _summary_quality_score(summary, evidence)
        self.assertLess(score, 0.8)
        self.assertLess(coverage, 1.0)
        self.assertTrue(any("missing_signals" in item for item in reasons))
        self.assertTrue(any("hard_facts_lt3" in item for item in reasons))

    def test_low_information_web_summary_is_degraded_to_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(
                listen_host="127.0.0.1",
                listen_port=8765,
                state_dir="state",
                obsidian=ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "股票"],
                    auto_topic_blocklist=["测试", "总结", "路径"],
                    auto_entity_pages=False,
                ),
                telegram=TelegramConfig(result_bot_token="token"),
                summarizer=SummarizerConfig(api_base_url="https://example.com", api_key="k", model="m", timeout_seconds=30),
                extractors=ExtractorConfig(),
            )
            state_dir = Path(tmp) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            jobs = JobStore(state_dir / "jobs")
            processor = _attach_note_renderer(WorkflowProcessor(cfg, jobs, FluffyWebSummarizer(), state_dir))
            processor.extractor = StaticExtractor(
                EvidenceBundle(
                    source_kind="url",
                    source_url="https://docs.openclaw.ai/",
                    platform_hint="docs",
                    title="OpenClaw 安装指南",
                    text="支持 WhatsApp、Telegram、Discord、iMessage。安装服务并完成 WhatsApp 配对后才能启动网关。",
                    evidence_type="visible_page_text",
                    coverage="full",
                    metadata={
                        "signals": {
                            "supported_platforms": ["WhatsApp", "Telegram", "Discord", "iMessage"],
                            "boundaries": ["没有完成 WhatsApp 配对前不能启动网关。"],
                        }
                    },
                )
            )
            processor.start()
            ingest = IngestRequest(
                chat_id="-1001",
                reply_to_message_id="42",
                request_id="job-low-info-web",
                source_kind="url",
                source_url="https://docs.openclaw.ai/",
                raw_text="https://docs.openclaw.ai/",
                dry_run=True,
            )
            processor.enqueue(ingest)
            processor._queue.join()
            job = jobs.load("job-low-info-web")
            processor.stop()
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "done")
            self.assertEqual(job.result["outcome"], "partial")
            self.assertTrue(any("summary_low_information_degraded" in item for item in job.warnings))
            self.assertLess(job.result["summary_quality"]["hard_fact_count"], 3)
            self.assertIn("证据缺口", "\n".join(job.result["summary"]["bullets"]))

    def test_normalize_source_url_for_cache_keeps_identity_and_strips_tracking(self) -> None:
        youtube = _normalize_source_url_for_cache(
            "https://www.youtube.com/watch?v=c7qJzG_swUE&utm_source=test&share_id=1"
        )
        self.assertEqual(youtube, "https://www.youtube.com/watch?v=c7qJzG_swUE")

        xhs = _normalize_source_url_for_cache(
            "https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7?xsec_token=abc&xsec_source=pc_feed"
        )
        self.assertEqual(xhs, "https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7")

    def test_video_missing_speech_track_adds_warning_but_job_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(
                listen_host="127.0.0.1",
                listen_port=8765,
                state_dir="state",
                obsidian=ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "股票"],
                    auto_topic_blocklist=["测试", "总结", "路径"],
                    auto_entity_pages=False,
                ),
                telegram=TelegramConfig(result_bot_token="token"),
                summarizer=SummarizerConfig(api_base_url="https://example.com", api_key="k", model="m", timeout_seconds=30),
                extractors=ExtractorConfig(),
            )
            state_dir = Path(tmp) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            jobs = JobStore(state_dir / "jobs")
            processor = _attach_note_renderer(WorkflowProcessor(cfg, jobs, WeakSummarizer(), state_dir))
            processor.extractor = StaticExtractor(
                EvidenceBundle(
                    source_kind="video_url",
                    source_url="https://example.com/video/a",
                    platform_hint="video",
                    title="视频测试",
                    text="这是一个仅有画面提示但没有字幕和转写的模拟视频证据。" * 8,
                    evidence_type="multimodal_video",
                    coverage="full",
                    metadata={
                        "tracks": {
                            "has_subtitle": False,
                            "has_transcript": False,
                            "has_keyframes": True,
                            "has_keyframe_ocr": True,
                        }
                    },
                )
            )
            processor.start()
            ingest = IngestRequest(
                chat_id="-1001",
                reply_to_message_id="42",
                request_id="job-video-gate-1",
                source_kind="video_url",
                source_url="https://example.com/video/a",
                dry_run=True,
            )
            processor.enqueue(ingest)
            processor._queue.join()
            job = jobs.load("job-video-gate-1")
            processor.stop()
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "done")
            self.assertTrue(any("video_evidence_incomplete" in item for item in job.warnings))
            self.assertIn("video_gate_reasons", job.result["evidence"]["metadata"])
            reasons = job.result["evidence"]["metadata"]["video_gate_reasons"]
            self.assertTrue(any("missing speech evidence" in item for item in reasons))
            self.assertEqual(job.result["outcome"], "refused")
            self.assertTrue(job.result["refusal_reason"])
            self.assertEqual(job.result["video_assessment"]["level"], "weak")
            self.assertTrue(any("video_incomplete" in item for item in job.result["summary_quality"]["reasons"]))

    def test_video_recovery_reextracts_and_applies_better_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig(
                listen_host="127.0.0.1",
                listen_port=8765,
                state_dir="state",
                obsidian=ObsidianConfig(
                    vault_path=tmp,
                    inbox_root="Inbox/OpenClaw",
                    topics_root="Topics",
                    entities_root="Entities",
                    auto_topic_whitelist=["AI", "股票"],
                    auto_topic_blocklist=["测试", "总结", "路径"],
                    auto_entity_pages=False,
                ),
                telegram=TelegramConfig(result_bot_token="token"),
                summarizer=SummarizerConfig(api_base_url="https://example.com", api_key="k", model="m", timeout_seconds=30),
                extractors=ExtractorConfig(),
            )
            state_dir = Path(tmp) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            jobs = JobStore(state_dir / "jobs")
            processor = _attach_note_renderer(WorkflowProcessor(cfg, jobs, StableSummarizer(), state_dir))
            processor.notifier = SilentNotifier()

            initial = EvidenceBundle(
                source_kind="video_url",
                source_url="https://example.com/video/recovery",
                platform_hint="video",
                title="视频补救测试",
                text="仅有页面描述，没有语音轨道文本。" * 10,
                evidence_type="multimodal_video",
                coverage="full",
                metadata={
                    "tracks": {
                        "has_subtitle": False,
                        "has_transcript": False,
                        "has_keyframes": False,
                        "has_keyframe_ocr": False,
                    }
                },
            )
            recovered = EvidenceBundle(
                source_kind="video_url",
                source_url="https://example.com/video/recovery",
                platform_hint="video",
                title="视频补救测试",
                text="这是补救抽取后的转写文本，包含完整步骤和关键结论。" * 30,
                evidence_type="multimodal_video",
                coverage="full",
                transcript="这是补救抽取后的转写文本。",
                metadata={
                    "tracks": {
                        "has_subtitle": False,
                        "has_transcript": True,
                        "has_keyframes": True,
                        "has_keyframe_ocr": True,
                    }
                },
            )
            processor.extractor = SequenceExtractor([initial, recovered])
            processor.video_summarizer = StableSummarizer()
            processor.start()
            ingest = IngestRequest(
                chat_id="-1001",
                reply_to_message_id="42",
                request_id="job-video-recovery-1",
                source_kind="video_url",
                source_url="https://example.com/video/recovery",
                dry_run=False,
            )
            processor.enqueue(ingest)
            processor._queue.join()
            job = jobs.load("job-video-recovery-1")
            processor.stop()

            self.assertIsNotNone(job)
            self.assertEqual(job.status, "done")
            self.assertTrue(any("video_recovery_applied" in item for item in job.warnings))
            self.assertIn("video_recovery", job.result)
            self.assertTrue(job.result["video_recovery"]["applied"])
            tracks = job.result["evidence"]["metadata"]["tracks"]
            self.assertTrue(tracks.get("has_transcript"))
            self.assertEqual(job.phase_status.get("notify"), "done")


if __name__ == "__main__":
    unittest.main()
