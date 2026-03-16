#!/usr/bin/env python3
"""Run bounded real end-to-end validation against production config."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openclaw_capture_workflow.config import AppConfig
from openclaw_capture_workflow.models import IngestRequest
from openclaw_capture_workflow.processor import WorkflowProcessor
from openclaw_capture_workflow.storage import JobStore
from openclaw_capture_workflow.summarizer import OpenAICompatibleSummarizer
from openclaw_capture_workflow.telegram import TelegramNotifier


@dataclass
class SampleSpec:
    sample_id: str
    kind: str
    label: str
    candidates: list[str]


@dataclass
class AttemptRecord:
    url: str
    request_id: str
    status: str = ""
    message: str = ""
    summary_model: str = ""
    summary_elapsed_seconds: float | None = None
    note_path: str | None = None
    notification_ok: bool | None = None
    notification_error: str | None = None
    warnings: list[str] = field(default_factory=list)
    telegram_text: str | None = None


@dataclass
class SampleResult:
    sample_id: str
    label: str
    kind: str
    success: bool
    chosen_url: str | None
    attempts: list[AttemptRecord]


class RecordingNotifier(TelegramNotifier):
    def __init__(self, bot_token: str) -> None:
        super().__init__(bot_token)
        self.payloads: dict[str, dict[str, str]] = {}

    def send_result(
        self,
        ingest,
        summary,
        note_path,
        structure_map,
        open_url,
        evidence=None,
        summary_model=None,
        summary_elapsed_seconds=None,
    ) -> None:
        payload = self.build_result_message_payload(
            ingest,
            summary,
            note_path,
            structure_map,
            open_url,
            evidence,
            summary_model,
            summary_elapsed_seconds,
        )
        self.payloads[ingest.request_id] = payload
        super().send_result(
            ingest,
            summary,
            note_path,
            structure_map,
            open_url,
            evidence,
            summary_model,
            summary_elapsed_seconds,
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--single-request-file", default="")
    parser.add_argument("--single-output-file", default="")
    parser.add_argument("--single-state-dir", default="")
    return parser.parse_args()


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _load_group_target() -> tuple[str, str | None]:
    historical_jobs = sorted((ROOT / "state" / "jobs").glob("tg-*.json"))
    counts: dict[str, int] = {}
    for path in historical_jobs:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        request = payload.get("request", {}) if isinstance(payload.get("request"), dict) else {}
        chat_id = str(request.get("chat_id", "")).strip()
        if chat_id.startswith("-100"):
            counts[chat_id] = counts.get(chat_id, 0) + 1
    if counts:
        chat_id = max(counts.items(), key=lambda item: item[1])[0]
        return chat_id, None
    payload = json.loads((ROOT / "scripts" / "robot_ingest_regression_cases.json").read_text(encoding="utf-8"))
    case = next(item for item in payload if item.get("entry_context", {}).get("chat_target") == "group_chat")
    request = case.get("payload", {})
    return str(request.get("chat_id", "")), None


def _backup_and_clear_obsidian(config: AppConfig, backup_root: Path) -> dict[str, str]:
    vault = Path(config.obsidian.vault_path).expanduser()
    inbox = vault / config.obsidian.inbox_root
    keyword_root = vault / config.obsidian.topics_root / "_Keywords"
    backup_root.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}
    if inbox.exists():
        target = backup_root / "Inbox_OpenClaw"
        shutil.copytree(inbox, target)
        shutil.rmtree(inbox)
        results["inbox_backup"] = str(target)
    inbox.mkdir(parents=True, exist_ok=True)
    if keyword_root.exists():
        target = backup_root / "Topics__Keywords"
        shutil.copytree(keyword_root, target)
        shutil.rmtree(keyword_root)
        results["keywords_backup"] = str(target)
    return results


def _default_samples() -> list[SampleSpec]:
    return [
        SampleSpec("bili_1", "video_url", "B站视频 1", ["https://www.bilibili.com/video/BV1WAcQzKEW8/"]),
        SampleSpec("bili_2", "video_url", "B站视频 2", ["https://www.bilibili.com/video/BV1bFPMzFEnd/"]),
        SampleSpec("xhs_video_1", "video_url", "小红书视频 1", ["https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7"]),
        SampleSpec("xhs_video_2", "video_url", "小红书视频 2", ["https://www.xiaohongshu.com/explore/6895cd780000000025026d99"]),
        SampleSpec("youtube_1", "video_url", "YouTube 视频 1", ["https://www.youtube.com/watch?v=c7qJzG_swUE"]),
        SampleSpec(
            "youtube_2",
            "video_url",
            "YouTube 视频 2",
            [
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://www.youtube.com/watch?v=jNQXAC9IVRw",
                "https://www.youtube.com/watch?v=M7lc1UVf-VE",
            ],
        ),
        SampleSpec(
            "xhs_note_1",
            "url",
            "小红书图文 1",
            ["https://www.xiaohongshu.com/explore/68e10f380000000007008c4b"],
        ),
        SampleSpec(
            "xhs_note_2",
            "url",
            "小红书图文 2",
            ["https://www.xiaohongshu.com/explore/69a3032400000000150305bb"],
        ),
        SampleSpec(
            "xhs_note_3",
            "url",
            "小红书图文 3",
            [
                "https://www.xiaohongshu.com/explore/69aea021000000001a028a59",
                "https://www.xiaohongshu.com/explore/69b41a4c000000002103b520",
            ],
        ),
        SampleSpec("web_1", "url", "普通图文网页", ["https://docs.openclaw.ai/"]),
    ]


def _sample_timeout_seconds(sample: SampleSpec, url: str) -> int:
    lowered = (url or "").lower()
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return 420
    if sample.kind == "video_url":
        return 300
    return 180


def _build_request(chat_id: str, reply_to_message_id: str | None, sample: SampleSpec, url: str) -> IngestRequest:
    request = IngestRequest(
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        request_id=f"live-{uuid.uuid4().hex[:12]}",
        source_kind=sample.kind,
        source_url=url,
        raw_text=url,
        dry_run=False,
    )
    lowered = url.lower()
    if "youtube.com" in lowered or "youtu.be" in lowered:
        request.video_probe_seconds = 180
    return request


def _attempt_succeeded(record: AttemptRecord) -> bool:
    return bool(record.status == "done" and record.note_path and record.notification_ok is True)


def _run_child(request_file: Path, output_file: Path, state_dir: Path, config_path: Path) -> None:
    config = AppConfig.load(str(config_path))
    config.execution.enable_summary_cache = False
    config.execution.cache_for_dry_run = False
    config.execution.cache_for_non_dry_run = False
    os.environ.setdefault("VIDEO_COOKIES_FROM_BROWSER", "chrome")
    jobs = JobStore(state_dir / "jobs")
    summarizer = OpenAICompatibleSummarizer(config.summarizer)
    processor = WorkflowProcessor(config, jobs, summarizer, state_dir)
    notifier = RecordingNotifier(config.telegram.result_bot_token)
    processor.notifier = notifier
    processor.start()
    try:
        request = IngestRequest.from_dict(json.loads(request_file.read_text(encoding="utf-8")))
        processor.enqueue(request)
        processor._queue.join()
        job = jobs.load(request.request_id)
        if job is None:
            record = AttemptRecord(url=request.source_url or "", request_id=request.request_id, status="missing_job", message="missing_job")
        else:
            result = job.result or {}
            note = result.get("note", {}) if isinstance(result.get("note"), dict) else {}
            payload = notifier.payloads.get(request.request_id, {})
            record = AttemptRecord(
                url=request.source_url or "",
                request_id=request.request_id,
                status=job.status,
                message=job.message,
                summary_model=str(result.get("summary_model") or ""),
                summary_elapsed_seconds=float(result.get("summary_elapsed_seconds")) if result.get("summary_elapsed_seconds") is not None else None,
                note_path=str(note.get("note_path")) if note.get("note_path") else None,
                notification_ok=job.notification.get("ok") if isinstance(job.notification, dict) else None,
                notification_error=job.notification.get("error") if isinstance(job.notification, dict) else None,
                warnings=list(job.warnings or []),
                telegram_text=str(payload.get("text")) if payload.get("text") else None,
            )
        output_file.write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        processor.stop()


def _run_single_attempt_parent(
    config_path: Path,
    state_dir: Path,
    request: IngestRequest,
    timeout_seconds: int,
) -> AttemptRecord:
    requests_dir = state_dir / "requests"
    outputs_dir = state_dir / "outputs"
    requests_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    request_file = requests_dir / f"{request.request_id}.json"
    output_file = outputs_dir / f"{request.request_id}.json"
    request_file.write_text(json.dumps(asdict(request), ensure_ascii=False, indent=2), encoding="utf-8")
    child_state_dir = state_dir / "sample_states" / request.request_id
    child_state_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--config",
        str(config_path),
        "--single-request-file",
        str(request_file),
        "--single-output-file",
        str(output_file),
        "--single-state-dir",
        str(child_state_dir),
    ]
    try:
        subprocess.run(cmd, check=True, timeout=timeout_seconds, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return AttemptRecord(
            url=request.source_url or "",
            request_id=request.request_id,
            status="timeout",
            message=f"timed_out_after_{timeout_seconds}s",
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or str(exc)
        if output_file.exists():
            try:
                data = json.loads(output_file.read_text(encoding="utf-8"))
                return AttemptRecord(**data)
            except Exception:
                pass
        return AttemptRecord(
            url=request.source_url or "",
            request_id=request.request_id,
            status="failed",
            message=detail[:1200],
        )
    if output_file.exists():
        data = json.loads(output_file.read_text(encoding="utf-8"))
        return AttemptRecord(**data)
    return AttemptRecord(url=request.source_url or "", request_id=request.request_id, status="failed", message="missing_output_file")


def _run_validation(config_path: Path) -> tuple[Path, Path, list[SampleResult], dict[str, str]]:
    timestamp = _timestamp()
    state_dir = ROOT / "state" / f"live_validation_{timestamp}"
    state_dir.mkdir(parents=True, exist_ok=True)
    report_json = ROOT / "state" / "reports" / f"live_validation_{timestamp}.json"
    report_json.parent.mkdir(parents=True, exist_ok=True)
    config = AppConfig.load(str(config_path))
    backup_root = ROOT / "state" / f"obsidian_cleanup_backup_{timestamp}"
    backup_info = _backup_and_clear_obsidian(config, backup_root)
    chat_id, reply_to = _load_group_target()
    sample_results: list[SampleResult] = []
    for sample in _default_samples():
        attempts: list[AttemptRecord] = []
        chosen = None
        success = False
        for url in sample.candidates:
            request = _build_request(chat_id, reply_to, sample, url)
            record = _run_single_attempt_parent(
                config_path,
                state_dir,
                request,
                _sample_timeout_seconds(sample, url),
            )
            attempts.append(record)
            if _attempt_succeeded(record):
                success = True
                chosen = url
                break
        sample_results.append(
            SampleResult(
                sample_id=sample.sample_id,
                label=sample.label,
                kind=sample.kind,
                success=success,
                chosen_url=chosen or (attempts[-1].url if attempts else None),
                attempts=attempts,
            )
        )
        report_payload = {
            "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "config": str(config_path),
            "backup": backup_info,
            "results": [asdict(item) for item in sample_results],
        }
        report_json.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return state_dir, report_json, sample_results, backup_info


def _md(text: str) -> str:
    return text.replace("\r", "").strip()


def _write_live_validation_doc(
    report_json: Path,
    results: list[SampleResult],
    backup_info: dict[str, str],
) -> Path:
    doc_path = ROOT / "docs" / "LIVE_VALIDATION.md"
    lines = [
        "# Live Validation",
        "",
        f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}",
        f"- JSON 报告: `{report_json.relative_to(ROOT)}`",
    ]
    if backup_info:
        lines.append("- Obsidian 备份:")
        for key, value in backup_info.items():
            lines.append(f"  - `{key}` -> `{value}`")
    lines.extend(["", "## Summary", ""])
    ok_count = sum(1 for item in results if item.success)
    lines.append(f"- 成功样本: `{ok_count}/{len(results)}`")
    failed = [item for item in results if not item.success]
    if failed:
        lines.append("- 失败样本:")
        for item in failed:
            lines.append(f"  - `{item.sample_id}` -> `{item.chosen_url or ''}`")
    lines.extend(["", "## Cases", ""])
    for item in results:
        lines.append(f"### {item.label}")
        lines.append(f"- 类型: `{item.kind}`")
        lines.append(f"- 成功: `{item.success}`")
        lines.append(f"- 最终 URL: `{item.chosen_url or ''}`")
        for attempt in item.attempts:
            lines.append(f"- 尝试: `{attempt.url}`")
            lines.append(f"  - job_status: `{attempt.status}` / `{attempt.message}`")
            lines.append(f"  - model: `{attempt.summary_model}` / elapsed: `{attempt.summary_elapsed_seconds}`")
            lines.append(f"  - note_path: `{attempt.note_path or ''}`")
            lines.append(f"  - telegram_ok: `{attempt.notification_ok}`")
            if attempt.notification_error:
                lines.append(f"  - telegram_error: `{attempt.notification_error}`")
            if attempt.warnings:
                lines.append("  - warnings:")
                for warning in attempt.warnings[:6]:
                    lines.append(f"    - `{warning}`")
            if attempt.telegram_text:
                lines.extend(["", "```text", _md(attempt.telegram_text), "```"])
        lines.append("")
    doc_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return doc_path


def main() -> int:
    args = _parse_args()
    config_path = Path(args.config).resolve()
    if args.single_request_file:
        if not args.single_output_file or not args.single_state_dir:
            raise SystemExit("single mode requires --single-output-file and --single-state-dir")
        _run_child(
            Path(args.single_request_file).resolve(),
            Path(args.single_output_file).resolve(),
            Path(args.single_state_dir).resolve(),
            config_path,
        )
        return 0
    state_dir, report_json, sample_results, backup_info = _run_validation(config_path)
    doc_path = _write_live_validation_doc(report_json, sample_results, backup_info)
    payload = {
        "state_dir": str(state_dir),
        "report_json": str(report_json),
        "doc_path": str(doc_path),
        "success_count": sum(1 for item in sample_results if item.success),
        "case_count": len(sample_results),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
