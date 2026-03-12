import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.config import AppConfig, ExtractorConfig, ObsidianConfig, SummarizerConfig, TelegramConfig
from openclaw_capture_workflow.models import EvidenceBundle, IngestRequest, SummaryResult
from openclaw_capture_workflow.processor import WorkflowProcessor, _extract_steps_from_text
from openclaw_capture_workflow.storage import JobStore


class FakeSummarizer:
    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
        return SummaryResult(
            title="测试标题",
            primary_topic="AI",
            secondary_topics=["股票"],
            entities=["OpenAI"],
            conclusion="基于证据生成的结论。",
            bullets=["要点一", "要点二", "要点三"],
            evidence_quotes=["证据一", "证据二"],
            coverage=evidence.coverage,
            confidence="high",
            note_tags=["AI"],
            follow_up_actions=[],
        )


class CountingSummarizer:
    def __init__(self) -> None:
        self.calls = 0

    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
        self.calls += 1
        return SummaryResult(
            title="计数摘要",
            primary_topic="AI",
            secondary_topics=[],
            entities=[],
            conclusion="缓存测试结论。",
            bullets=["要点A", "要点B", "要点C"],
            evidence_quotes=["证据A"],
            coverage=evidence.coverage,
            confidence="high",
            note_tags=[],
            follow_up_actions=[],
        )


class FakeNotifier:
    def __init__(self) -> None:
        self.sent = []

    def send_result(self, ingest, summary, note_path, structure_map, open_url) -> None:
        self.sent.append((ingest.request_id, note_path, structure_map, open_url))


class FailingNotifier:
    def send_result(self, ingest, summary, note_path, structure_map, open_url) -> None:
        raise RuntimeError("HTTP Error 400: Bad Request")


class BrokenSummarizer:
    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
        raise RuntimeError("invalid summary json")


class StaticExtractor:
    def __init__(self, evidence: EvidenceBundle) -> None:
        self._evidence = evidence

    def extract(self, request: IngestRequest) -> EvidenceBundle:
        return self._evidence


class WorkflowProcessorTest(unittest.TestCase):
    def test_extract_steps_from_text_skips_overlong_command_block(self) -> None:
        text = (
            "命令：/install-skill https://example.com/a.skill 方法二：手动下载安装 "
            "从仓库下载 skill 文件 在对话中使用 /install-skill 并上传 "
            "安装后无需额外配置，直接自然语言提问即可自动激活。"
        )
        steps = _extract_steps_from_text(text)
        self.assertEqual(steps, [])

    def test_enqueue_and_process_text_request(self) -> None:
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
            processor = WorkflowProcessor(cfg, jobs, FakeSummarizer(), state_dir)
            processor.notifier = FakeNotifier()
            processor.start()
            ingest = IngestRequest(
                chat_id="-1001",
                reply_to_message_id="42",
                request_id="job-1",
                source_kind="pasted_text",
                raw_text="这是一段需要归档的文字。",
                dry_run=True,
            )
            processor.enqueue(ingest)
            processor._queue.join()
            job = jobs.load("job-1")
            processor.stop()
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "done")

    def test_dry_run_is_read_only_and_returns_preview(self) -> None:
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
            processor = WorkflowProcessor(cfg, jobs, FakeSummarizer(), state_dir)
            notifier = FakeNotifier()
            processor.notifier = notifier
            processor.start()
            ingest = IngestRequest(
                chat_id="-1001",
                reply_to_message_id="42",
                request_id="job-dry-preview",
                source_kind="pasted_text",
                raw_text="这是一段需要归档的文字，长度足够用于摘要测试。",
                dry_run=True,
            )
            processor.enqueue(ingest)
            processor._queue.join()
            job = jobs.load("job-dry-preview")
            processor.stop()
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "done")
            self.assertEqual(job.phase_status["write_note"], "skipped")
            self.assertEqual(job.phase_status["notify"], "skipped")
            self.assertFalse(job.notification["attempted"])
            self.assertIn("note_preview", job.result)
            self.assertNotIn("note", job.result)
            preview_file = job.result["note_preview"].get("preview_file")
            self.assertTrue(preview_file)
            self.assertTrue(Path(str(preview_file)).exists())
            self.assertEqual(len(notifier.sent), 0)
            vault_notes = [path for path in Path(tmp).rglob("*.md") if "state/previews" not in str(path)]
            self.assertEqual(vault_notes, [])

    def test_notify_failure_keeps_job_done_with_warning(self) -> None:
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
            processor = WorkflowProcessor(cfg, jobs, FakeSummarizer(), state_dir)
            processor.notifier = FailingNotifier()
            processor.start()
            ingest = IngestRequest(
                chat_id="-1001",
                reply_to_message_id="42",
                request_id="job-notify-warning",
                source_kind="pasted_text",
                raw_text="这是一段需要归档并发送通知的文字，长度足够用于摘要测试。",
                dry_run=False,
            )
            processor.enqueue(ingest)
            processor._queue.join()
            job = jobs.load("job-notify-warning")
            processor.stop()
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "done")
            self.assertEqual(job.message, "completed_with_warnings")
            self.assertEqual(job.phase_status["notify"], "failed")
            self.assertTrue(job.notification["attempted"])
            self.assertFalse(job.notification["ok"])
            self.assertIn("HTTP Error 400", job.notification["error"])
            self.assertTrue(any("notification_error" in item for item in job.warnings))
            self.assertIn("notification_error", job.result)
            note_path = Path(tmp) / str(job.result["note"]["note_path"])
            self.assertTrue(note_path.exists())

    def test_summarizer_failure_falls_back_with_metadata(self) -> None:
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
            cfg.execution.dry_run_skip_model_call = False
            state_dir = Path(tmp) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            jobs = JobStore(state_dir / "jobs")
            processor = WorkflowProcessor(cfg, jobs, BrokenSummarizer(), state_dir)
            processor.notifier = FakeNotifier()
            processor.start()
            ingest = IngestRequest(
                chat_id="-1001",
                reply_to_message_id="42",
                request_id="job-fallback-summary",
                source_kind="pasted_text",
                raw_text="原始证据内容，包含多个可抽取句子。第一条。第二条。第三条。",
                dry_run=True,
            )
            processor.enqueue(ingest)
            processor._queue.join()
            job = jobs.load("job-fallback-summary")
            processor.stop()
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "done")
            self.assertEqual(job.result["summary_mode"], "fallback")
            self.assertIn("summary_error", job.result)
            self.assertTrue(any("summarizer_fallback" in item for item in job.warnings))

    def test_temp_artifacts_are_cleaned_after_job(self) -> None:
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
            processor = WorkflowProcessor(cfg, jobs, FakeSummarizer(), state_dir)
            processor.notifier = FakeNotifier()

            temp_image = state_dir / "temp-shot.jpg"
            temp_image.write_text("x", encoding="utf-8")
            temp_dir = state_dir / "artifacts" / "job-cleanup"
            temp_dir.mkdir(parents=True, exist_ok=True)
            (temp_dir / "frame-001.jpg").write_text("x", encoding="utf-8")

            processor.extractor = StaticExtractor(
                EvidenceBundle(
                    source_kind="pasted_text",
                    source_url=None,
                    platform_hint="local",
                    title="标题",
                    text="正文包含足够长度用于摘要处理，并明确记录临时截图与关键帧清理逻辑，确保任务收尾时删除临时文件。",
                    evidence_type="raw_text",
                    coverage="full",
                    metadata={
                        "temp_image_refs": [str(temp_image)],
                        "temp_artifact_dirs": [str(temp_dir)],
                    },
                )
            )
            processor.start()
            ingest = IngestRequest(
                chat_id="-1001",
                reply_to_message_id="42",
                request_id="job-cleanup",
                source_kind="pasted_text",
                source_url=None,
                raw_text="任意文本",
                dry_run=True,
            )
            processor.enqueue(ingest)
            processor._queue.join()
            job = jobs.load("job-cleanup")
            processor.stop()
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "done")
            self.assertFalse(temp_image.exists())
            self.assertFalse(temp_dir.exists())

    def test_video_cost_estimate_warns_when_over_budget(self) -> None:
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
            processor = WorkflowProcessor(cfg, jobs, FakeSummarizer(), state_dir)
            processor.notifier = FakeNotifier()
            processor.extractor = StaticExtractor(
                EvidenceBundle(
                    source_kind="video_url",
                    source_url="https://example.com/video/budget",
                    platform_hint="video",
                    title="视频预算测试",
                    text="视频内容正文" * 80,
                    evidence_type="multimodal_video",
                    coverage="full",
                    transcript="这是转写文本",
                    metadata={
                        "video_duration_seconds": 1200,
                        "tracks": {
                            "has_subtitle": True,
                            "has_transcript": True,
                            "has_keyframes": True,
                            "has_keyframe_ocr": True,
                        },
                    },
                )
            )
            processor.start()
            ingest = IngestRequest(
                chat_id="-1001",
                reply_to_message_id="42",
                request_id="job-video-budget-1",
                source_kind="video_url",
                source_url="https://example.com/video/budget",
                dry_run=True,
            )
            processor.enqueue(ingest)
            processor._queue.join()
            job = jobs.load("job-video-budget-1")
            processor.stop()
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "done")
            self.assertIn("video_cost_estimate", job.result)
            estimate = job.result["video_cost_estimate"]
            self.assertGreater(estimate["total_rmb"], estimate["budget_rmb"])
            self.assertTrue(any("video_cost_over_budget" in item for item in job.warnings))

    def test_dry_run_skip_model_call_enabled_saves_cost(self) -> None:
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
            counting = CountingSummarizer()
            state_dir = Path(tmp) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            jobs = JobStore(state_dir / "jobs")
            processor = WorkflowProcessor(cfg, jobs, counting, state_dir)
            processor.notifier = FakeNotifier()
            processor.start()
            ingest = IngestRequest(
                chat_id="-1001",
                reply_to_message_id="42",
                request_id="job-dry-skip-model",
                source_kind="pasted_text",
                raw_text="这是一段用于 dry_run 成本节省验证的文本。",
                dry_run=True,
            )
            processor.enqueue(ingest)
            processor._queue.join()
            job = jobs.load("job-dry-skip-model")
            processor.stop()
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "done")
            self.assertEqual(counting.calls, 0)
            self.assertEqual(job.result["summary_mode"], "fallback_dry_run")
            self.assertEqual(job.result["summary_model"], "fallback")
            self.assertEqual(job.result["summary_model_chain"], ["fallback"])
            self.assertTrue(any("dry_run_skip_model_call" in item for item in job.warnings))

    def test_summary_cache_reuses_model_result_for_same_url(self) -> None:
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
            cfg.execution.dry_run_skip_model_call = False
            counting = CountingSummarizer()
            state_dir = Path(tmp) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            jobs = JobStore(state_dir / "jobs")
            processor = WorkflowProcessor(cfg, jobs, counting, state_dir)
            processor.notifier = FakeNotifier()
            processor.extractor = StaticExtractor(
                EvidenceBundle(
                    source_kind="url",
                    source_url="https://example.com/article?utm_source=test",
                    platform_hint="web",
                    title="缓存验证标题",
                    text="缓存验证正文，长度足够并保持稳定，确保两次任务的证据指纹一致。" * 3,
                    evidence_type="visible_page_text",
                    coverage="full",
                )
            )
            processor.start()
            processor.enqueue(
                IngestRequest(
                    chat_id="-1001",
                    reply_to_message_id="42",
                    request_id="job-cache-1",
                    source_kind="url",
                    source_url="https://example.com/article?utm_source=test",
                    dry_run=False,
                )
            )
            processor.enqueue(
                IngestRequest(
                    chat_id="-1001",
                    reply_to_message_id="42",
                    request_id="job-cache-2",
                    source_kind="url",
                    source_url="https://example.com/article?utm_source=again",
                    dry_run=False,
                )
            )
            processor._queue.join()
            job1 = jobs.load("job-cache-1")
            job2 = jobs.load("job-cache-2")
            processor.stop()
            self.assertIsNotNone(job1)
            self.assertIsNotNone(job2)
            self.assertEqual(counting.calls, 1)
            self.assertIn("summary_cache", job1.result)
            self.assertIn("summary_cache", job2.result)
            self.assertFalse(job1.result["summary_cache"]["hit"])
            self.assertTrue(job2.result["summary_cache"]["hit"])
            self.assertTrue(any("summary_cache_hit" in item for item in job2.warnings))


if __name__ == "__main__":
    unittest.main()
