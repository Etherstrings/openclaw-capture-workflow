#!/usr/bin/env python3
"""Run real video truth validation and emit per-case previews/reports."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

TARGET_STORY_BLOCK_CASE_IDS = {"milky_bilibili_video"}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _render_preview(case_id: str, evidence, summary, recall, result: dict) -> str:
    story_blocks = []
    if isinstance(getattr(evidence, "metadata", None), dict):
        maybe_blocks = evidence.metadata.get("video_story_blocks", [])
        if isinstance(maybe_blocks, list):
            story_blocks = [item for item in maybe_blocks if isinstance(item, dict)]
    viewer_feedback = []
    if isinstance(getattr(evidence, "metadata", None), dict):
        maybe_feedback = evidence.metadata.get("viewer_feedback", [])
        if isinstance(maybe_feedback, list):
            viewer_feedback = [str(item).strip() for item in maybe_feedback if str(item).strip()]
    lines = [
        f"# {case_id}",
        "",
        f"- source_url: {evidence.source_url}",
        f"- coverage: {evidence.coverage}",
        f"- video_assessment: {result.get('video_assessment', {}).get('level', '')}",
        "",
        "## 原始证据概览",
        "",
        evidence.text[:2000].strip() or "（空）",
        "",
        "## 枚举点识别",
        "",
        f"- detected_count: {len(recall.detected_points)}",
        f"- retained_count: {len(recall.summary_points)}",
        f"- outline_detected: {str(recall.outline_detected).lower()}",
        f"- order_preserved: {str(recall.order_preserved).lower()}",
        f"- missing_points: {recall.missing_points}",
        f"- extra_points: {recall.extra_points}",
        "",
        "## 主题块",
        "",
        f"- story_block_count: {recall.story_block_count}",
        f"- story_blocks_detected: {str(recall.story_blocks_detected).lower()}",
    ]
    for block in story_blocks[:6]:
        label = str(block.get("label", "")).strip()
        summary_text = str(block.get("summary", "")).strip()
        evidence_items = block.get("evidence", [])
        evidence_text = ""
        if isinstance(evidence_items, list) and evidence_items:
            evidence_text = " | evidence: " + " / ".join([str(item).strip() for item in evidence_items[:2] if str(item).strip()])
        lines.append(f"- [{label}] {summary_text}{evidence_text}")
    lines.extend(
        [
            "",
            "## 观众反馈",
            "",
            f"- viewer_feedback_count: {len(viewer_feedback)}",
        ]
    )
    if viewer_feedback:
        lines.extend(f"- {item}" for item in viewer_feedback[:5])
    else:
        lines.append("- （空）")
    lines.extend(
        [
            "",
            "## 质量检查",
            "",
            f"- workflow_required: {str(recall.workflow_required).lower()}",
            f"- workflow_hit: {str(recall.workflow_hit).lower()}",
            f"- risk_required: {str(recall.risk_required).lower()}",
            f"- risk_hit: {str(recall.risk_hit).lower()}",
            f"- viewer_feedback_available: {str(recall.viewer_feedback_available).lower()}",
            f"- viewer_feedback_hit: {str(recall.viewer_feedback_hit).lower()}",
            f"- bullet_quality_ok: {str(recall.bullet_quality_ok).lower()}",
            "",
        "## 最终总结结果",
        "",
        f"- conclusion: {summary.conclusion}",
        "- bullets:",
        ]
    )
    lines.extend(f"  - {item}" for item in summary.bullets)
    lines.append("")
    lines.append("## 判定")
    lines.append("")
    lines.append(f"- passed: {str(result['passed']).lower()}")
    lines.append(f"- root_cause: {result['root_cause']}")
    return "\n".join(lines) + "\n"


def _render_report(results: list[dict], cases_path: str) -> str:
    lines = [
        "# Video Truth Validation Report",
        "",
        f"- generated_at: {datetime.now().isoformat(timespec='seconds')}",
        f"- cases_path: {cases_path}",
        "",
        "## Summary",
        "",
        "| case_id | model | passed | outline_detected | story_blocks | workflow | risk | feedback | bullet_quality | root_cause | preview |",
        "|---|---|---|---|---:|---|---|---|---|---|---|",
    ]
    for item in results:
        for model_label, model_result in item["models"].items():
            lines.append(
                f"| {item['case_id']} | {model_label} | {str(model_result['passed']).lower()} | "
                f"{str(model_result['outline_detected']).lower()} | {model_result['story_block_count']} | "
                f"{str(model_result['workflow_hit']).lower()} | {str(model_result['risk_hit']).lower()} | "
                f"{str(model_result['viewer_feedback_hit']).lower()} | {str(model_result['bullet_quality_ok']).lower()} | "
                f"{model_result['root_cause']} | {model_result['preview_file']} |"
            )
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    root = _project_root()
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from openclaw_capture_workflow.accuracy_eval import load_eval_cases
    from openclaw_capture_workflow.config import AppConfig, VideoSummaryConfig
    from openclaw_capture_workflow.extractor import EvidenceExtractor
    from openclaw_capture_workflow.models import IngestRequest
    from openclaw_capture_workflow.summarizer import OpenAICompatibleSummarizer
    from openclaw_capture_workflow.video_experiment_summarizer import AiHubMixGeminiSummarizer
    from openclaw_capture_workflow.video_truth_eval import evaluate_enumeration_recall
    from openclaw_capture_workflow.processor import _video_assessment

    parser = argparse.ArgumentParser(description="Run real video truth validation")
    parser.add_argument("--config", default=str(root / "config.json"))
    parser.add_argument("--cases", default=str(root / "scripts" / "accuracy_eval_cases.new_videos.json"))
    args = parser.parse_args()

    config = AppConfig.load(args.config)
    base_dir = Path(args.config).resolve().parent
    state_dir = config.ensure_state_dirs(base_dir)
    preview_dir = state_dir / "previews"
    report_dir = state_dir / "reports"
    preview_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    extractor = EvidenceExtractor(config, state_dir / "artifacts")
    current_summarizer = OpenAICompatibleSummarizer(config.summarizer)
    gemini_pro_cfg = VideoSummaryConfig(
        provider=config.video_summary.provider,
        transport=config.video_summary.transport,
        api_base_url=config.video_summary.api_base_url,
        api_key=config.video_summary.api_key,
        model=config.video_summary.model,
        fallback_model=config.video_summary.fallback_model,
        timeout_seconds=config.video_summary.timeout_seconds,
    )
    gemini_flash_cfg = VideoSummaryConfig(
        provider=config.video_summary.provider,
        transport=config.video_summary.transport,
        api_base_url=config.video_summary.api_base_url,
        api_key=config.video_summary.api_key,
        model=config.video_summary.fallback_model,
        fallback_model=config.video_summary.fallback_model,
        timeout_seconds=config.video_summary.timeout_seconds,
    )
    model_runners = [
        ("current_model", current_summarizer),
        ("gemini_2_5_pro", AiHubMixGeminiSummarizer(gemini_pro_cfg)),
        ("gemini_2_5_flash", AiHubMixGeminiSummarizer(gemini_flash_cfg)),
    ]
    cases = [case for case in load_eval_cases(args.cases) if case.source_kind == "video_url"]
    use_structured_analyzer_path = Path(args.cases).name.endswith("enumerated_videos.json")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    results: list[dict] = []

    for index, case in enumerate(cases, start=1):
        ingest = IngestRequest(
            chat_id="-1",
            reply_to_message_id="1",
            request_id=f"video-truth-{case.case_id}-{timestamp}-{index}",
            source_kind=case.source_kind,
            source_url=case.source_url,
            raw_text=case.raw_text or case.source_url,
            image_refs=list(case.image_refs),
            platform_hint=case.platform_hint,
            requested_output_lang="zh-CN",
            dry_run=not use_structured_analyzer_path,
            video_probe_seconds=case.video_probe_seconds,
            force_full_video=case.force_full_video,
        )
        evidence = extractor.extract(ingest)
        model_results = {}
        previews = []
        for model_label, summarizer in model_runners:
            blocked_text = (evidence.text or "").lower()
            summary_error = ""
            try:
                summary = summarizer.summarize(evidence)
                recall = evaluate_enumeration_recall(evidence, summary)
                passed = True
                root_cause = "pass"
                if any(token in blocked_text for token in ["错误号: 412", "安全限制", "ip存在风险", "request was rejected"]):
                    passed = False
                    root_cause = "platform_blocked"
                elif not recall.bullet_quality_ok:
                    passed = False
                    root_cause = "raw_transcript_bullets"
                elif case.case_id in TARGET_STORY_BLOCK_CASE_IDS and not recall.story_blocks_detected:
                    passed = False
                    root_cause = "story_blocks_missing"
                elif recall.workflow_required and not recall.workflow_hit:
                    passed = False
                    root_cause = "workflow_missing"
                elif recall.risk_required and not recall.risk_hit:
                    passed = False
                    root_cause = "risk_missing"
                elif recall.viewer_feedback_available and not recall.viewer_feedback_hit:
                    passed = False
                    root_cause = "viewer_feedback_missing"
                elif recall.outline_detected:
                    if len(recall.summary_points) < len(recall.detected_points):
                        passed = False
                        root_cause = "summary_compressed"
                    elif recall.missing_points:
                        passed = False
                        root_cause = "summary_compressed"
                    elif not recall.order_preserved:
                        passed = False
                        root_cause = "order_mismatch"
                elif Path(args.cases).name.endswith("enumerated_videos.json"):
                    passed = False
                    root_cause = "outline_not_detected"
            except Exception as exc:
                summary_error = str(exc)
                summary = None
                recall = None
                passed = False
                root_cause = "summary_failed"
            preview_path = preview_dir / f"video-truth-{case.case_id}-{model_label}.md"
            preview_path.write_text(
                _render_preview(
                    f"{case.case_id}:{model_label}",
                    evidence,
                    summary or type("_Summary", (), {"conclusion": summary_error or "summary failed", "bullets": []})(),
                    recall
                    or type(
                        "_Recall",
                        (),
                        {
                            "detected_points": [],
                            "summary_points": [],
                            "order_preserved": False,
                            "missing_points": [],
                            "extra_points": [],
                            "outline_detected": False,
                            "story_block_count": 0,
                            "story_blocks_detected": False,
                            "workflow_required": False,
                            "workflow_hit": True,
                            "risk_required": False,
                            "risk_hit": True,
                            "viewer_feedback_available": False,
                            "viewer_feedback_hit": True,
                            "bullet_quality_ok": True,
                        },
                    )(),
                    {"passed": passed, "root_cause": root_cause, "video_assessment": _video_assessment(evidence, config) or {}},
                ),
                encoding="utf-8",
            )
            model_results[model_label] = {
                "passed": passed,
                "detected_count": len(recall.detected_points) if recall else 0,
                "retained_count": len(recall.summary_points) if recall else 0,
                "missing_points": recall.missing_points if recall else [],
                "extra_points": recall.extra_points if recall else [],
                "order_preserved": recall.order_preserved if recall else False,
                "outline_detected": recall.outline_detected if recall else False,
                "structure_mode_used": recall.structure_mode_used if recall else "summary_failed",
                "story_block_count": recall.story_block_count if recall else 0,
                "story_blocks_detected": recall.story_blocks_detected if recall else False,
                "workflow_required": recall.workflow_required if recall else False,
                "workflow_hit": recall.workflow_hit if recall else True,
                "risk_required": recall.risk_required if recall else False,
                "risk_hit": recall.risk_hit if recall else True,
                "viewer_feedback_available": recall.viewer_feedback_available if recall else False,
                "viewer_feedback_hit": recall.viewer_feedback_hit if recall else True,
                "bullet_quality_ok": recall.bullet_quality_ok if recall else True,
                "root_cause": root_cause,
                "summary_error": summary_error or None,
                "preview_file": str(preview_path),
            }
            previews.append(str(preview_path))
        results.append(
            {
                "case_id": case.case_id,
                "source_url": case.source_url,
                "coverage": evidence.coverage,
                "video_assessment": _video_assessment(evidence, config) or {},
                "models": model_results,
                "preview_files": previews,
            }
        )

    json_path = report_dir / f"video_truth_validation_{timestamp}.json"
    md_path = report_dir / f"video_truth_validation_{timestamp}.md"
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cases_path": str(Path(args.cases).resolve()),
        "results": results,
    }
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_report(results, str(Path(args.cases).resolve())), encoding="utf-8")
    print(f"report_json={json_path}")
    print(f"report_markdown={md_path}")
    for item in results:
        for model_label, model_result in item["models"].items():
            print(
                f"case={item['case_id']} model={model_label} passed={str(model_result['passed']).lower()} "
                f"detected={model_result['detected_count']} retained={model_result['retained_count']} "
                f"outline={str(model_result['outline_detected']).lower()} story_blocks={model_result['story_block_count']} "
                f"root_cause={model_result['root_cause']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
