#!/usr/bin/env python3
"""Run repeatable dry-run validation on randomly selected public Bilibili videos."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import random
import sys
from urllib import parse as urlparse
from urllib import request as urlrequest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openclaw_capture_workflow.config import AppConfig
from openclaw_capture_workflow.models import IngestRequest
from openclaw_capture_workflow.processor import WorkflowProcessor
from openclaw_capture_workflow.storage import JobStore
from openclaw_capture_workflow.summarizer import OpenAICompatibleSummarizer
from openclaw_capture_workflow.video_summary_score import score_video_summary_from_job_payload


@dataclass
class CategorySpec:
    category_id: str
    label: str
    keywords: list[str]
    fallback_urls: list[str]


@dataclass
class ValidationScore:
    topic_clarity: int
    mainline_coverage: int
    factual_fidelity: int
    boundary_honesty: int
    readability: int
    total: int
    notes: list[str]


@dataclass
class ValidationCaseResult:
    category_id: str
    label: str
    keyword: str
    source_url: str
    status: str
    outcome: str
    summary_mode: str
    title: str
    conclusion: str
    warnings: list[str]
    quality_gate: dict
    video_assessment: dict
    root_cause: str
    score: ValidationScore


DEFAULT_CATEGORIES = [
    CategorySpec(
        category_id="review",
        label="测评/横评",
        keywords=["手机 评测", "显卡 横评", "笔记本 评测"],
        fallback_urls=[
            "https://www.bilibili.com/video/BV1eP4y1x7aR/",
            "https://www.bilibili.com/video/BV1o9cSzpExL/",
        ],
    ),
    CategorySpec(
        category_id="tutorial",
        label="教程/职场",
        keywords=["面试 问题 职场", "Python 教程", "前端 教程"],
        fallback_urls=[
            "https://www.bilibili.com/video/BV1ggpcevEgk/",
            "https://www.bilibili.com/video/BV1y4411p74E/",
        ],
    ),
    CategorySpec(
        category_id="history",
        label="科普/历史",
        keywords=["历史 科普", "人类 起源 科普", "二战 历史"],
        fallback_urls=[
            "https://www.bilibili.com/video/BV157411g7VC/",
            "https://www.bilibili.com/video/BV1WAcQzKEW8/",
        ],
    ),
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run random Bilibili validation in dry-run mode")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--seed", type=int, default=20260317)
    parser.add_argument("--per-category", type=int, default=1)
    return parser.parse_args()


def _fetch_bilibili_search(keyword: str, *, page_size: int = 10) -> list[str]:
    params = urlparse.urlencode(
        {
            "search_type": "video",
            "keyword": keyword,
            "page": 1,
            "page_size": page_size,
        }
    )
    req = urlrequest.Request(
        "https://api.bilibili.com/x/web-interface/search/type?" + params,
        headers={"User-Agent": "Mozilla/5.0 OpenClawCaptureWorkflow/0.1"},
    )
    with urlrequest.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    results = data.get("result", []) if isinstance(data.get("result"), list) else []
    urls: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        arcurl = str(item.get("arcurl", "")).strip()
        if arcurl.startswith("https://www.bilibili.com/video/") and arcurl not in urls:
            urls.append(arcurl)
    return urls


def _choose_urls(spec: CategorySpec, rng: random.Random, per_category: int) -> list[tuple[str, str]]:
    chosen: list[tuple[str, str]] = []
    keywords = list(spec.keywords)
    rng.shuffle(keywords)
    for keyword in keywords:
        try:
            urls = _fetch_bilibili_search(keyword)
        except Exception:
            urls = []
        if not urls:
            continue
        rng.shuffle(urls)
        for url in urls[:per_category]:
            chosen.append((keyword, url))
            if len(chosen) >= per_category:
                return chosen
    fallback_urls = list(spec.fallback_urls)
    rng.shuffle(fallback_urls)
    for url in fallback_urls[:per_category]:
        chosen.append(("fallback", url))
    return chosen


def _score_case(job_payload: dict) -> ValidationScore:
    result = job_payload.get("result", {}) if isinstance(job_payload.get("result"), dict) else {}
    existing = result.get("video_clarity_score", {}) if isinstance(result.get("video_clarity_score"), dict) else {}
    score_payload = existing if existing and "total" in existing else score_video_summary_from_job_payload(job_payload)
    return ValidationScore(
        topic_clarity=int(score_payload.get("topic_clarity", 0) or 0),
        mainline_coverage=int(score_payload.get("mainline_coverage", 0) or 0),
        factual_fidelity=int(score_payload.get("factual_fidelity", 0) or 0),
        boundary_honesty=int(score_payload.get("boundary_honesty", 0) or 0),
        readability=int(score_payload.get("readability", 0) or 0),
        total=int(score_payload.get("total", 0) or 0),
        notes=[str(item) for item in score_payload.get("notes", [])] if isinstance(score_payload.get("notes"), list) else [],
    )


def _render_markdown(cases: list[ValidationCaseResult], output_json: Path) -> str:
    lines = [
        "# Random Bilibili Validation",
        "",
        f"- generated_at: {datetime.now().isoformat(timespec='seconds')}",
        f"- json_report: {output_json}",
        "",
        "| 类别 | 关键词 | URL | status | outcome | 分数 | 结论 |",
        "|---|---|---|---|---|---:|---|",
    ]
    for case in cases:
        lines.append(
            f"| {case.label} | {case.keyword} | {case.source_url} | {case.status} | {case.outcome} | {case.score.total}/10 | {case.conclusion} |"
        )
    lines.append("")
    for case in cases:
        lines.extend(
            [
                f"## {case.label}",
                "",
                f"- keyword: `{case.keyword}`",
                f"- source_url: {case.source_url}",
                f"- status: `{case.status}`",
                f"- outcome: `{case.outcome}`",
                f"- summary_mode: `{case.summary_mode}`",
                f"- score: `{case.score.total}/10`",
                f"- sub_scores: topic={case.score.topic_clarity}, mainline={case.score.mainline_coverage}, fidelity={case.score.factual_fidelity}, boundary={case.score.boundary_honesty}, readability={case.score.readability}",
                f"- title: {case.title}",
                f"- conclusion: {case.conclusion}",
                f"- quality_gate: `{json.dumps(case.quality_gate, ensure_ascii=False)}`",
                f"- video_assessment: `{json.dumps(case.video_assessment, ensure_ascii=False)}`",
                f"- root_cause: `{case.root_cause}`",
            ]
        )
        if case.warnings:
            lines.append("- warnings:")
            lines.extend([f"  - {item}" for item in case.warnings[:6]])
        if case.score.notes:
            lines.append("- notes:")
            lines.extend([f"  - {item}" for item in case.score.notes])
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _parse_args()
    rng = random.Random(args.seed)
    config = AppConfig.load(args.config)
    config.execution.enable_summary_cache = False
    config.execution.cache_for_dry_run = False
    config.execution.cache_for_non_dry_run = False
    base_dir = Path(args.config).resolve().parent
    state_dir = config.ensure_state_dirs(base_dir)
    jobs = JobStore(state_dir / "jobs")
    summarizer = OpenAICompatibleSummarizer(config.summarizer)
    processor = WorkflowProcessor(config, jobs, summarizer, state_dir)
    processor.start()
    cases: list[ValidationCaseResult] = []
    try:
        for spec in DEFAULT_CATEGORIES:
            for keyword, url in _choose_urls(spec, rng, max(1, int(args.per_category))):
                request_id = f"random-bili-{spec.category_id}-{rng.randint(100000, 999999)}"
                ingest = IngestRequest(
                    chat_id="-1",
                    reply_to_message_id="1",
                    request_id=request_id,
                    source_kind="video_url",
                    source_url=url,
                    raw_text=url,
                    dry_run=True,
                )
                processor.enqueue(ingest)
                processor._queue.join()
                job = jobs.load(request_id)
                if job is None:
                    continue
                result = job.result or {}
                summary = result.get("summary", {}) if isinstance(result.get("summary"), dict) else {}
                score = _score_case(job.to_dict())
                quality_gate = result.get("quality_gate", {}) if isinstance(result.get("quality_gate"), dict) else {}
                root_cause = ""
                reasons = quality_gate.get("reasons", []) if isinstance(quality_gate.get("reasons"), list) else []
                if reasons:
                    root_cause = str(reasons[0])
                elif job.warnings:
                    root_cause = str(job.warnings[0])
                cases.append(
                    ValidationCaseResult(
                        category_id=spec.category_id,
                        label=spec.label,
                        keyword=keyword,
                        source_url=url,
                        status=job.status,
                        outcome=str(result.get("outcome", "")),
                        summary_mode=str(result.get("summary_mode", "")),
                        title=str(summary.get("title", "")),
                        conclusion=str(summary.get("conclusion", "")),
                        warnings=list(job.warnings or []),
                        quality_gate=quality_gate,
                        video_assessment=result.get("video_assessment", {}) if isinstance(result.get("video_assessment"), dict) else {},
                        root_cause=root_cause,
                        score=score,
                    )
                )
    finally:
        processor.stop()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = state_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    output_json = report_dir / f"random_bilibili_validation_{timestamp}.json"
    output_md = report_dir / f"random_bilibili_validation_{timestamp}.md"
    output_json.write_text(
        json.dumps([asdict(case) for case in cases], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_md.write_text(_render_markdown(cases, output_json), encoding="utf-8")
    print(str(output_json))
    print(str(output_md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
