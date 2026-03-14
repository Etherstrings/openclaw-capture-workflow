"""Iterative recognition runner: baseline -> search enrichment -> choose best."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from .accuracy_eval import (
    EvalExpectation,
    build_fix_suggestion,
    diagnose_root_cause,
    evaluate_extract_step,
    evaluate_note_step,
    evaluate_signal_step,
    evaluate_summary_step,
)
from .analyzer.llm import OpenAIResponsesClient
from .analyzer.models import ExtractedContent, SectionResult, StructuredDocument
from .analyzer.service import analyze_url
from .config import AppConfig
from .extractor import _extract_skill_signals
from .iterative_cases import RecognitionCase, load_auto_case_inbox, load_recognition_cases, merge_recognition_cases
from .models import EvidenceBundle, SummaryResult
from .note_renderer import OpenAICompatibleNoteRenderer
from .obsidian import ObsidianWriter
from .search_fallback import SearchEvidenceBundle, run_search_enrichment


@dataclass
class IterativeCandidate:
    label: str
    document: StructuredDocument
    warnings: List[str]
    overall_score: float
    passed: bool
    root_cause: str
    fix_suggestion: str
    missing: List[str]
    forbidden_hits: List[str]
    preview_content: str
    summary_mode: str
    diagnosis: Dict[str, Any]
    steps: Dict[str, Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["document"] = self.document.to_dict()
        return payload


@dataclass
class IterativeCaseResult:
    case_id: str
    baseline: Dict[str, Any]
    searched: Dict[str, Any]
    chosen: Dict[str, Any]
    search_trace: Dict[str, Any]
    diagnosis: Dict[str, Any]
    delta: Dict[str, Any]
    preview_files: Dict[str, str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _document_to_text(document: StructuredDocument) -> str:
    parts: List[str] = []
    if document.title:
        parts.append(f"标题: {document.title}")
    if document.summary:
        parts.append(f"摘要: {document.summary}")
    if document.sections:
        parts.append("[章节]")
        for section in document.sections:
            heading = (section.heading or "").strip()
            content = section.content.strip()
            if heading and content:
                parts.append(f"{heading}: {content}")
            elif content:
                parts.append(content)
    if document.images:
        parts.append("[图片]")
        for item in document.images[:4]:
            segment = " | ".join(part for part in [item.caption, item.alt, item.context, item.src] if part)
            if segment:
                parts.append(segment)
    if document.videos:
        parts.append("[视频]")
        for item in document.videos[:3]:
            details = [part for part in [item.provider, item.src] if part]
            if details:
                parts.append(" | ".join(details))
            for summary in item.frame_summaries[:3]:
                if summary.strip():
                    parts.append(summary.strip())
    if document.tables:
        parts.append("[表格]")
        for table in document.tables[:3]:
            if table.caption:
                parts.append(f"表格标题: {table.caption}")
            if table.headers:
                parts.append("表头: " + " | ".join(table.headers))
            for row in table.rows[:2]:
                if row:
                    parts.append("行: " + " | ".join(row))
    return "\n".join(parts).strip()


def _document_to_evidence(case: RecognitionCase, document: StructuredDocument, warnings: List[str], *, extra_text: str = "") -> EvidenceBundle:
    text = _document_to_text(document)
    if extra_text.strip():
        text = text.rstrip() + "\n\n[搜索补充]\n" + extra_text.strip()
    metadata: Dict[str, Any] = {"structured_document": document.to_dict()}
    signals = _extract_skill_signals(text, case.source_url)
    if signals:
        metadata["signals"] = signals
    if warnings:
        metadata["fetch_warnings"] = list(warnings)
    return EvidenceBundle(
        source_kind=case.source_kind,
        source_url=case.source_url,
        platform_hint=case.platform_hint,
        title=document.title,
        text=text,
        evidence_type="structured_document",
        coverage="full" if text else "partial",
        metadata=metadata,
    )


def _document_to_summary(document: StructuredDocument) -> SummaryResult:
    primary_topic = (document.sections[0].heading if document.sections and document.sections[0].heading else document.title).strip()
    bullets: List[str] = []
    for section in document.sections[:5]:
        heading = (section.heading or "").strip()
        content = section.content.strip()
        if heading and content:
            bullets.append(f"{heading}: {content[:160]}")
        elif content:
            bullets.append(content[:160])
    if not bullets and document.summary:
        bullets.append(document.summary[:160])
    evidence_quotes = [section.content[:80] for section in document.sections[:3] if section.content.strip()]
    return SummaryResult(
        title=document.title,
        primary_topic=primary_topic or document.title,
        secondary_topics=[],
        entities=[],
        conclusion=document.summary,
        bullets=bullets[:6],
        evidence_quotes=evidence_quotes[:3],
        coverage="full" if document.sections else "partial",
        confidence="medium",
        note_tags=[],
        follow_up_actions=[],
    )


def _score_candidate(case: RecognitionCase, document: StructuredDocument, warnings: List[str], writer: ObsidianWriter, *, extra_text: str = "") -> IterativeCandidate:
    evidence = _document_to_evidence(case, document, warnings, extra_text=extra_text)
    summary = _document_to_summary(document)
    note_preview = writer.preview(summary, evidence)
    note_content = str(note_preview.get("content", ""))
    expect = case.expect if case.has_expectations() else EvalExpectation()

    extract_step = evaluate_extract_step(evidence, expect)
    signal_step = evaluate_signal_step(evidence, expect)
    summary_step = evaluate_summary_step(summary, expect)
    note_step = evaluate_note_step(note_content, expect)

    base_score = (
        extract_step.score * 0.35
        + signal_step.score * 0.20
        + summary_step.score * 0.30
        + note_step.score * 0.15
    )
    warning_penalty = min(0.25, 0.05 * len(warnings))
    overall_score = max(0.0, base_score - warning_penalty)
    root_cause = diagnose_root_cause(
        extract_step=extract_step,
        signal_step=signal_step,
        summary_step=summary_step,
        note_step=note_step,
        summary_mode="iterative",
        summary_error="",
    )

    missing_union: List[str] = []
    forbidden_union: List[str] = []
    for step in [extract_step, signal_step, summary_step, note_step]:
        for item in step.missing:
            if item not in missing_union:
                missing_union.append(item)
        for item in step.forbidden_hits:
            if item not in forbidden_union:
                forbidden_union.append(item)
    passed = overall_score >= 0.72 and not forbidden_union

    diagnosis = {
        "root_cause": root_cause,
        "warning_count": len(warnings),
        "warnings": list(warnings[:5]),
        "baseline_weakness": build_fix_suggestion(root_cause),
    }
    return IterativeCandidate(
        label="",
        document=document,
        warnings=list(warnings),
        overall_score=round(overall_score, 4),
        passed=passed,
        root_cause=root_cause,
        fix_suggestion=build_fix_suggestion(root_cause),
        missing=missing_union,
        forbidden_hits=forbidden_union,
        preview_content=note_content,
        summary_mode="iterative",
        diagnosis=diagnosis,
        steps={
            "extract": extract_step.to_dict(),
            "signals": signal_step.to_dict(),
            "summary": summary_step.to_dict(),
            "note": note_step.to_dict(),
        },
    )


def _structured_document_from_search(
    case: RecognitionCase,
    baseline: StructuredDocument,
    search_bundle: SearchEvidenceBundle,
    config: AppConfig,
) -> tuple[StructuredDocument, List[str]]:
    warnings = list(search_bundle.warnings)
    if not search_bundle.evidence_text.strip():
        return baseline, warnings
    extracted = ExtractedContent(
        title=baseline.title,
        main_text=_document_to_text(baseline) + "\n\n" + search_bundle.evidence_text,
        sections=list(baseline.sections) + [SectionResult(heading="Search Enrichment", level=2, content=search_bundle.evidence_text)],
        images=[],
        videos=[],
        tables=list(baseline.tables),
    )
    client = OpenAIResponsesClient(config)
    try:
        document = client.generate_document(
            extracted=extracted,
            requested_output_lang="zh-CN",
            screenshot_path=None,
        )
        return document, warnings
    except Exception as exc:
        warnings.append(f"search_regeneration_failed:{exc}")
        fallback = StructuredDocument(
            title=baseline.title,
            summary=(baseline.summary + " " + search_bundle.evidence_text[:200]).strip()[:400],
            sections=extracted.sections[:8],
            images=list(baseline.images),
            videos=list(baseline.videos),
            tables=list(baseline.tables),
        )
        return fallback, warnings


def _collect_links_and_headings(document: StructuredDocument) -> tuple[List[str], List[str]]:
    links: List[str] = []
    headings: List[str] = []
    for image in document.images:
        if image.src and image.src not in links:
            links.append(image.src)
    for video in document.videos:
        if video.src and video.src not in links:
            links.append(video.src)
    for section in document.sections:
        if section.heading:
            headings.append(section.heading)
        for match in re.findall(r"https?://\S+", section.content):
            url = match.rstrip(")]}>,.;")
            if url not in links:
                links.append(url)
    return links, headings


def _compute_delta(case: RecognitionCase, baseline: IterativeCandidate, searched: IterativeCandidate, chosen_label: str) -> Dict[str, Any]:
    baseline_links, baseline_headings = _collect_links_and_headings(baseline.document)
    searched_links, searched_headings = _collect_links_and_headings(searched.document)
    added_links = [item for item in searched_links if item not in baseline_links][:5]
    added_headings = [item for item in searched_headings if item not in baseline_headings][:5]
    return {
        "chosen": chosen_label,
        "score_delta": round(searched.overall_score - baseline.overall_score, 4),
        "added_links": added_links,
        "added_headings": added_headings,
        "warning_delta": len(searched.warnings) - len(baseline.warnings),
        "expectation_helped": [
            item
            for item in baseline.missing
            if item not in searched.missing
        ][:6],
    }


def _build_case_diagnosis(baseline: IterativeCandidate, searched: IterativeCandidate, chosen_label: str) -> Dict[str, Any]:
    return {
        "case_problem_type": baseline.root_cause,
        "baseline_weakness": baseline.fix_suggestion,
        "search_helped": searched.overall_score > baseline.overall_score,
        "chosen": chosen_label,
        "chosen_root_cause": searched.root_cause if chosen_label == "searched" else baseline.root_cause,
    }


def _save_preview(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def _render_iterative_report(results: List[IterativeCaseResult], case_source: str, cases_path: str, auto_inbox: str) -> str:
    lines: List[str] = []
    lines.append("# Iterative Recognition Report")
    lines.append("")
    lines.append(f"- generated_at: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- case_source: {case_source}")
    lines.append(f"- cases_path: {cases_path}")
    lines.append(f"- auto_inbox: {auto_inbox}")
    lines.append("")
    lines.append("## Case Overview")
    lines.append("")
    lines.append("| case_id | baseline_score | searched_score | chosen | diagnosis | previews |")
    lines.append("|---|---:|---:|---|---|---|")
    for item in results:
        lines.append(
            f"| {item.case_id} | {item.baseline['overall_score']} | {item.searched['overall_score']} | "
            f"{item.chosen['label']} | {item.diagnosis['case_problem_type']} | "
            f"{item.preview_files['baseline']}<br>{item.preview_files['searched']}<br>{item.preview_files['final']} |"
        )
    lines.append("")
    lines.append("## Root Cause Stats")
    lines.append("")
    stats: Dict[str, int] = {}
    for item in results:
        key = str(item.diagnosis.get("case_problem_type", "pass"))
        stats[key] = stats.get(key, 0) + 1
    for key, value in sorted(stats.items(), key=lambda pair: pair[0]):
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## Details")
    lines.append("")
    for item in results:
        lines.append(f"### {item.case_id}")
        lines.append("")
        lines.append(f"- chosen: {item.chosen['label']}")
        lines.append(f"- baseline_score: {item.baseline['overall_score']}")
        lines.append(f"- searched_score: {item.searched['overall_score']}")
        lines.append(f"- baseline_root_cause: {item.baseline['root_cause']}")
        lines.append(f"- searched_root_cause: {item.searched['root_cause']}")
        lines.append(f"- search_helped: {str(item.diagnosis['search_helped']).lower()}")
        lines.append(f"- added_links: {item.delta['added_links']}")
        lines.append(f"- added_headings: {item.delta['added_headings']}")
        lines.append(f"- expectation_helped: {item.delta['expectation_helped']}")
        lines.append(f"- baseline_preview: {item.preview_files['baseline']}")
        lines.append(f"- searched_preview: {item.preview_files['searched']}")
        lines.append(f"- final_preview: {item.preview_files['final']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def run_iterative_recognition(
    *,
    config_path: str,
    cases_path: str,
    case_source: str = "mixed",
    auto_inbox_path: str = "",
    search_template: str = "https://duckduckgo.com/html/?q={query}",
    max_results: int = 5,
    max_pages: int = 2,
) -> Dict[str, Any]:
    config = AppConfig.load(config_path)
    base_dir = Path(config_path).resolve().parent
    state_dir = config.ensure_state_dirs(base_dir)
    manual_cases = load_recognition_cases(cases_path, default_provenance="manual") if Path(cases_path).exists() else []
    auto_inbox = auto_inbox_path or str((state_dir / "cases" / "inbox.jsonl").resolve())
    auto_cases = load_auto_case_inbox(auto_inbox)
    if case_source == "manual":
        cases = manual_cases
    elif case_source == "auto":
        cases = auto_cases
    else:
        cases = merge_recognition_cases(manual_cases, auto_cases)

    writer = ObsidianWriter(
        config.obsidian,
        renderer=OpenAICompatibleNoteRenderer(config.summarizer),
        materials_root=state_dir / "materials",
    )
    preview_dir = state_dir / "previews"
    report_dir = state_dir / "reports"
    preview_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    results: List[IterativeCaseResult] = []
    for case in cases:
        outcome = analyze_url(
            url=case.source_url or "",
            requested_output_lang="zh-CN",
            config=config,
            state_dir=state_dir,
        )
        baseline_doc = outcome.document
        baseline_candidate = _score_candidate(case, baseline_doc, outcome.warnings, writer)
        baseline_candidate.label = "baseline"

        search_bundle = run_search_enrichment(
            case,
            baseline_doc,
            search_template=search_template,
            max_results=max_results,
            max_pages=max_pages,
        )
        searched_doc, searched_warnings = _structured_document_from_search(case, baseline_doc, search_bundle, config)
        searched_candidate = _score_candidate(
            case,
            searched_doc,
            outcome.warnings + searched_warnings,
            writer,
            extra_text=search_bundle.evidence_text,
        )
        searched_candidate.label = "searched"

        chosen_candidate = searched_candidate if searched_candidate.overall_score > baseline_candidate.overall_score else baseline_candidate
        diagnosis = _build_case_diagnosis(baseline_candidate, searched_candidate, chosen_candidate.label)
        delta = _compute_delta(case, baseline_candidate, searched_candidate, chosen_candidate.label)

        baseline_preview = _save_preview(
            preview_dir / f"iter-{case.case_id}-baseline.md",
            baseline_candidate.preview_content,
        )
        searched_preview = _save_preview(
            preview_dir / f"iter-{case.case_id}-searched.md",
            searched_candidate.preview_content,
        )
        final_preview = _save_preview(
            preview_dir / f"iter-{case.case_id}-final.md",
            chosen_candidate.preview_content,
        )

        results.append(
            IterativeCaseResult(
                case_id=case.case_id,
                baseline=baseline_candidate.to_dict(),
                searched=searched_candidate.to_dict(),
                chosen=chosen_candidate.to_dict(),
                search_trace=search_bundle.to_dict(),
                diagnosis=diagnosis,
                delta=delta,
                preview_files={
                    "baseline": baseline_preview,
                    "searched": searched_preview,
                    "final": final_preview,
                },
            )
        )

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config_path": str(Path(config_path).resolve()),
        "cases_path": str(Path(cases_path).resolve()),
        "case_source": case_source,
        "auto_inbox_path": str(Path(auto_inbox).resolve()),
        "case_count": len(results),
        "results": [item.to_dict() for item in results],
    }
    report_path = report_dir / f"iterative_recognition_{timestamp}.md"
    json_path = report_dir / f"iterative_recognition_{timestamp}.json"
    report_path.write_text(_render_iterative_report(results, case_source, cases_path, auto_inbox), encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_markdown"] = str(report_path)
    report["report_json"] = str(json_path)
    return report
