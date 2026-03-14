"""Video truth validation helpers for outline retention and narrative quality."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Dict, List

from .models import EvidenceBundle, SummaryResult
from .summarizer import _extract_explicit_video_outline, _extract_video_outline
from .video_story_blocks import get_qualified_video_story_blocks, get_viewer_feedback


@dataclass
class EnumerationRecall:
    outline_detected: bool
    detected_points: List[str]
    summary_points: List[str]
    matched_points: List[str]
    missing_points: List[str]
    extra_points: List[str]
    order_preserved: bool
    structure_mode_used: str = ""
    story_blocks_detected: bool = False
    story_block_count: int = 0
    story_block_summaries: List[str] = field(default_factory=list)
    workflow_required: bool = False
    workflow_hit: bool = True
    risk_required: bool = False
    risk_hit: bool = True
    viewer_feedback_available: bool = False
    viewer_feedback_hit: bool = True
    bullet_quality_ok: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize_point(value: str) -> str:
    text = str(value).strip().lower()
    if ". " in text[:4]:
        text = text.split(". ", 1)[1]
    return text


def _story_block_hit(summary_corpus: str, block: dict[str, object] | None) -> bool:
    if not block:
        return True
    summary_text = _normalize_point(str(block.get("summary", "")))
    if summary_text and summary_text in summary_corpus:
        return True
    evidence_items = block.get("evidence", [])
    if isinstance(evidence_items, list):
        for item in evidence_items[:2]:
            normalized = _normalize_point(str(item))
            if normalized and len(normalized) >= 8 and normalized in summary_corpus:
                return True
    return False


def _looks_like_raw_transcript_bullet(value: str, evidence: EvidenceBundle) -> bool:
    text = _normalize_point(value)
    if not text:
        return False
    if len(text) >= 90:
        return True
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 32:
        return False
    transcript_corpus = re.sub(r"\s+", "", (evidence.transcript or evidence.text or "").lower())
    if compact and compact in transcript_corpus and not re.search(r"[，。；：:,]", text):
        return True
    return False


def evaluate_enumeration_recall(evidence: EvidenceBundle, summary: SummaryResult) -> EnumerationRecall:
    explicit_detected = _extract_explicit_video_outline(evidence, summary.bullets)
    detected = _extract_video_outline(evidence, summary.bullets)
    summary_points: List[str] = []
    for item in summary.bullets:
        text = str(item).strip()
        if text:
            summary_points.append(text)

    story_blocks = get_qualified_video_story_blocks(evidence)
    non_feedback_story_blocks = [block for block in story_blocks if str(block.get("label", "")).strip() != "viewer_feedback"]
    workflow_block = next((block for block in story_blocks if str(block.get("label", "")).strip() == "workflow"), None)
    risk_block = next((block for block in story_blocks if str(block.get("label", "")).strip() == "risk"), None)
    viewer_feedback_block = next((block for block in story_blocks if str(block.get("label", "")).strip() == "viewer_feedback"), None)
    viewer_feedback = get_viewer_feedback(evidence) if story_blocks else []
    summary_corpus = "\n".join([summary.conclusion, *summary_points]).lower()

    if detected:
        normalized_detected = [_normalize_point(item) for item in detected]
        normalized_summary = [_normalize_point(item) for item in summary_points]
        matched = [item for item in normalized_detected if item in normalized_summary]
        missing = [item for item in normalized_detected if item not in normalized_summary]
        extra = [item for item in normalized_summary if item not in normalized_detected]
        ordered_summary = [item for item in normalized_summary if item in normalized_detected]
        if explicit_detected:
            structure_mode = "explicit_outline_video_summary"
        elif non_feedback_story_blocks:
            structure_mode = "story_block_video_summary"
        else:
            structure_mode = "generic_video_summary"
        return EnumerationRecall(
            outline_detected=True,
            detected_points=detected,
            summary_points=summary_points,
            matched_points=matched,
            missing_points=missing,
            extra_points=extra,
            order_preserved=ordered_summary == matched,
            structure_mode_used=structure_mode,
            story_blocks_detected=len(non_feedback_story_blocks) >= 3,
            story_block_count=len(non_feedback_story_blocks),
            story_block_summaries=[str(block.get("summary", "")).strip() for block in non_feedback_story_blocks if str(block.get("summary", "")).strip()],
            workflow_required=workflow_block is not None,
            workflow_hit=_story_block_hit(summary_corpus, workflow_block),
            risk_required=risk_block is not None,
            risk_hit=_story_block_hit(summary_corpus, risk_block),
            viewer_feedback_available=bool(viewer_feedback or viewer_feedback_block),
            viewer_feedback_hit=_story_block_hit(summary_corpus, viewer_feedback_block),
            bullet_quality_ok=not any(_looks_like_raw_transcript_bullet(item, evidence) for item in summary_points),
        )

    return EnumerationRecall(
        outline_detected=False,
        detected_points=[],
        summary_points=summary_points,
        matched_points=[],
        missing_points=[],
        extra_points=[],
        order_preserved=True,
        structure_mode_used="generic_video_summary",
        story_blocks_detected=len(non_feedback_story_blocks) >= 3,
        story_block_count=len(non_feedback_story_blocks),
        story_block_summaries=[str(block.get("summary", "")).strip() for block in non_feedback_story_blocks if str(block.get("summary", "")).strip()],
        workflow_required=workflow_block is not None,
        workflow_hit=_story_block_hit(summary_corpus, workflow_block),
        risk_required=risk_block is not None,
        risk_hit=_story_block_hit(summary_corpus, risk_block),
        viewer_feedback_available=bool(viewer_feedback or viewer_feedback_block),
        viewer_feedback_hit=_story_block_hit(summary_corpus, viewer_feedback_block),
        bullet_quality_ok=not any(_looks_like_raw_transcript_bullet(item, evidence) for item in summary_points),
    )
