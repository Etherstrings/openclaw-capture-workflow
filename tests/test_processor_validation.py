import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.config import AppConfig, ExtractorConfig, ObsidianConfig, SummarizerConfig, TelegramConfig
from openclaw_capture_workflow.models import EvidenceBundle, IngestRequest, SummaryResult
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


class SilentNotifier:
    def send_result(self, ingest, summary, note_path, structure_map, open_url) -> None:
        return None


class ProcessorValidationTest(unittest.TestCase):
    def test_fails_when_evidence_is_just_url(self) -> None:
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
            self.assertEqual(job.status, "failed")
            self.assertIn("insufficient evidence", job.error)

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
            self.assertTrue(any("missing speech track" in item for item in reasons))
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
