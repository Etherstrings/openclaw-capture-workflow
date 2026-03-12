import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.config import AppConfig, ExtractorConfig, ObsidianConfig, SummarizerConfig, TelegramConfig
from openclaw_capture_workflow.models import EvidenceBundle, IngestRequest, SummaryResult
from openclaw_capture_workflow.processor import WorkflowProcessor
from openclaw_capture_workflow.storage import JobStore


CASES_PATH = Path(__file__).resolve().parents[1] / "scripts" / "robot_ingest_regression_cases.json"


class ReplaySummarizer:
    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
        return SummaryResult(
            title="回放测试",
            primary_topic="测试",
            secondary_topics=[],
            entities=[],
            conclusion="回放链路可正常返回结果。",
            bullets=["项目名称: 回放项目", "GitHub地址: https://github.com/example/repo", "关键命令: /install-skill demo.skill"],
            evidence_quotes=["回放链路", "入口上下文"],
            coverage=evidence.coverage,
            confidence="high",
            note_tags=[],
            follow_up_actions=["执行命令：/install-skill demo.skill", "验证动作：检查结果消息"],
        )


class SilentNotifier:
    def send_result(self, ingest, summary, note_path, structure_map, open_url) -> None:
        return None


class StaticExtractor:
    def extract(self, request: IngestRequest) -> EvidenceBundle:
        return EvidenceBundle(
            source_kind=request.source_kind,
            source_url=request.source_url,
            platform_hint=request.platform_hint,
            title="回放证据",
            text="这是用于机器人入口回放的固定证据文本，包含项目、链接、执行动作以及额外上下文说明，用于确保 URL 和 mixed 场景不会因为证据长度过短而被门控拒绝。",
            evidence_type="raw_text",
            coverage="full",
            metadata={
                "signals": {
                    "projects": ["example/repo"],
                    "links": ["https://github.com/example/repo"],
                    "commands": ["/install-skill demo.skill"],
                },
                "content_profile": {
                    "kind": "installation_tutorial",
                    "required_signal_keys": ["projects", "links", "commands"],
                    "optional_signal_keys": [],
                    "require_action_checklist": True,
                    "require_project_section": True,
                },
                "signal_requirements": {
                    "kind": "installation_tutorial",
                    "required_signal_keys": ["projects", "links", "commands"],
                    "required_signal_values": [
                        {"key": "projects", "value": "example/repo"},
                        {"key": "links", "value": "https://github.com/example/repo"},
                        {"key": "commands", "value": "/install-skill demo.skill"},
                    ],
                    "require_action_checklist": True,
                    "require_project_section": True,
                },
                "evidence_sources": ["user_raw_text"],
            },
        )


def _config(tmp: str) -> AppConfig:
    return AppConfig(
        listen_host="127.0.0.1",
        listen_port=8765,
        state_dir="state",
        obsidian=ObsidianConfig(
            vault_path=tmp,
            inbox_root="Inbox/OpenClaw",
            topics_root="Topics",
            entities_root="Entities",
            auto_topic_whitelist=["AI", "GitHub"],
            auto_topic_blocklist=["测试", "路径"],
            auto_entity_pages=False,
        ),
        telegram=TelegramConfig(result_bot_token="token"),
        summarizer=SummarizerConfig(api_base_url="https://example.com", api_key="k", model="m", timeout_seconds=30),
        extractors=ExtractorConfig(),
    )


class RobotEntryReplayTest(unittest.TestCase):
    def test_saved_cases_cover_group_and_direct_entry(self) -> None:
        cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 6)
        targets = {item["entry_context"]["chat_target"] for item in cases}
        self.assertIn("group_chat", targets)
        self.assertIn("direct_chat", targets)
        for item in cases:
            payload = item["payload"]
            for field in [
                "chat_id",
                "reply_to_message_id",
                "request_id",
                "source_kind",
                "source_url",
                "raw_text",
                "image_refs",
                "platform_hint",
                "requested_output_lang",
            ]:
                self.assertIn(field, payload)

    def test_saved_cases_roundtrip_into_ingest_request(self) -> None:
        cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        for item in cases:
            ingest = IngestRequest.from_dict(item["payload"])
            self.assertEqual(ingest.request_id, item["payload"]["request_id"])
            self.assertEqual(ingest.source_kind, item["payload"]["source_kind"])
            self.assertEqual(ingest.requested_output_lang, "zh-CN")

    def test_mixed_payload_keeps_text_link_and_images(self) -> None:
        cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        mixed_case = next(item for item in cases if item["payload"]["source_kind"] == "mixed")
        ingest = IngestRequest.from_dict(mixed_case["payload"])
        self.assertTrue(ingest.source_url)
        self.assertTrue((ingest.raw_text or "").strip())
        self.assertGreaterEqual(len(ingest.image_refs), 1)

    def test_group_and_direct_replay_populate_entry_context(self) -> None:
        cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        group_case = next(item for item in cases if item["entry_context"]["chat_target"] == "group_chat")
        direct_case = next(item for item in cases if item["entry_context"]["chat_target"] == "direct_chat")
        for case, expected_target in [(group_case, "group_chat"), (direct_case, "direct_chat")]:
            with tempfile.TemporaryDirectory() as tmp:
                state_dir = Path(tmp) / "state"
                state_dir.mkdir(parents=True, exist_ok=True)
                jobs = JobStore(state_dir / "jobs")
                processor = WorkflowProcessor(_config(tmp), jobs, ReplaySummarizer(), state_dir)
                processor.extractor = StaticExtractor()
                processor.notifier = SilentNotifier()
                processor.start()
                ingest = IngestRequest.from_dict(case["payload"])
                processor.enqueue(ingest)
                processor._queue.join()
                job = jobs.load(ingest.request_id)
                processor.stop()
                self.assertIsNotNone(job)
                self.assertEqual(job.status, "done")
                self.assertEqual(job.result["entry_context"]["chat_target"], expected_target)
                self.assertEqual(job.result["entry_context"]["source_kind"], ingest.source_kind)


if __name__ == "__main__":
    unittest.main()
