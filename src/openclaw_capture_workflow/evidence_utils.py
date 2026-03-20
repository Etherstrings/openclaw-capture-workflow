"""Helpers for structured evidence and capture-state bookkeeping."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .models import CaptureManifest, CaptureRecord, EvidenceBundle, EvidenceItem


PRIMARY_SPEECH_SOURCES = {"subtitle", "asr"}


def build_capture_manifest() -> CaptureManifest:
    manifest = CaptureManifest()
    manifest.ensure_defaults()
    return manifest


def mark_capture(
    manifest: CaptureManifest,
    slot: str,
    *,
    status: str,
    provider: str = "",
    reason: str = "",
    artifact_refs: Iterable[str] | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    manifest.ensure_defaults()
    refs = [str(item).strip() for item in (artifact_refs or []) if str(item).strip()]
    manifest.items[slot] = CaptureRecord(
        status=status,
        provider=provider.strip(),
        reason=reason.strip(),
        artifact_refs=refs,
        details=dict(details or {}),
    )


def add_evidence_item(
    items: list[EvidenceItem],
    *,
    modality: str,
    source: str,
    provider: str,
    text: str,
    timestamp_start: float | None = None,
    timestamp_end: float | None = None,
    confidence: float = 0.0,
    artifact_ref: str | None = None,
    is_primary: bool = False,
) -> None:
    cleaned = str(text or "").strip()
    if not cleaned:
        return
    items.append(
        EvidenceItem(
            modality=modality,
            source=source,
            provider=provider,
            text=cleaned,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,
            confidence=confidence,
            artifact_ref=artifact_ref,
            is_primary=is_primary,
        )
    )


def speech_items(evidence: EvidenceBundle) -> list[EvidenceItem]:
    return [item for item in evidence.evidence_items if item.source in PRIMARY_SPEECH_SOURCES and item.text.strip()]


def visual_items(evidence: EvidenceBundle) -> list[EvidenceItem]:
    return [item for item in evidence.evidence_items if item.source in {"keyframes", "keyframe_ocr", "page_metadata", "browser_render"} and item.text.strip()]


def merged_text_from_items(items: list[EvidenceItem], fallback_blocks: Iterable[str] = ()) -> str:
    blocks: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        cleaned = item.text.strip()
        if not cleaned:
            continue
        key = (item.source, cleaned)
        if key in seen:
            continue
        seen.add(key)
        prefix = ""
        if item.source == "page_metadata":
            prefix = "[视频元数据]"
        elif item.source == "subtitle":
            prefix = "[字幕]"
        elif item.source == "asr":
            prefix = "[转写]"
        elif item.source == "keyframes":
            prefix = "[关键帧]"
        elif item.source == "keyframe_ocr":
            prefix = "[关键帧OCR]"
        elif item.source == "timeline_highlights":
            prefix = "[视频时间线要点]"
        elif item.source == "browser_render":
            prefix = "[视频页面补充]" if "browser_snapshot" in item.provider else "[页面补充]"
        elif item.source == "browser_screenshot_ocr":
            prefix = "[OCR补充]"
        elif item.source == "uploaded_image_ocr":
            prefix = "[上传图片OCR]"
        elif item.source == "web_html":
            prefix = "[网页正文]"
        elif item.source == "github_repo":
            prefix = "[GitHub正文]"
        if prefix:
            blocks.append(prefix + "\n" + cleaned)
        else:
            blocks.append(cleaned)
    for block in fallback_blocks:
        cleaned = str(block or "").strip()
        if cleaned:
            blocks.append(cleaned)
    return "\n\n".join(blocks).strip()


def collect_timeline_segments(evidence: EvidenceBundle) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for item in speech_items(evidence):
        if not item.text.strip():
            continue
        segments.append(
            {
                "source": item.source,
                "provider": item.provider,
                "start": item.timestamp_start,
                "end": item.timestamp_end,
                "text": item.text.strip(),
                "confidence": item.confidence,
            }
        )
    if not segments and isinstance(evidence.metadata, dict):
        raw_lines: list[str] = []
        for key in ["transcript_timeline_lines", "timeline_highlights"]:
            values = evidence.metadata.get(key, [])
            if isinstance(values, list):
                raw_lines.extend(str(item) for item in values if str(item).strip())
        pattern = re.compile(r"^\[(\d{2}:\d{2}(?::\d{2})?)\]\s*(.+)$")

        def _parse_seconds(label: str) -> float | None:
            parts = [int(item) for item in label.split(":")]
            if len(parts) == 2:
                minutes, seconds = parts
                return float(minutes * 60 + seconds)
            if len(parts) == 3:
                hours, minutes, seconds = parts
                return float(hours * 3600 + minutes * 60 + seconds)
            return None

        for raw in raw_lines:
            match = pattern.match(str(raw).strip())
            if not match:
                continue
            start = _parse_seconds(match.group(1))
            text = str(match.group(2)).strip()
            if start is None or not text:
                continue
            segments.append(
                {
                    "source": "asr",
                    "provider": "metadata_timeline_lines",
                    "start": start,
                    "end": None,
                    "text": text,
                    "confidence": 0.6,
                }
            )
    segments.sort(key=lambda item: (item.get("start") is None, item.get("start") or 0.0))
    return segments


def build_evidence_digest(evidence: EvidenceBundle) -> dict[str, Any]:
    manifest = evidence.capture_manifest.to_dict()
    sources = sorted({item.source for item in evidence.evidence_items if item.text.strip()})
    primary_sources = sorted({item.source for item in evidence.evidence_items if item.is_primary and item.text.strip()})
    speech_chars = sum(len(item.text.strip()) for item in speech_items(evidence))
    visual_chars = sum(len(item.text.strip()) for item in visual_items(evidence))
    return {
        "source_kind": evidence.source_kind,
        "evidence_type": evidence.evidence_type,
        "coverage": evidence.coverage,
        "merged_text_chars": len((evidence.merged_text or evidence.text or "").strip()),
        "speech_chars": speech_chars,
        "visual_chars": visual_chars,
        "item_count": len([item for item in evidence.evidence_items if item.text.strip()]),
        "sources": sources,
        "primary_sources": primary_sources,
        "capture_manifest": manifest,
    }
