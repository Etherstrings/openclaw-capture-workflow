"""Case registry and auto-inbox helpers for iterative recognition."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional

from .accuracy_eval import EvalExpectation, EvalCase


SUPPORTED_SOURCE_KINDS = {"url", "video_url"}
AUTO_CASE_SCORE_THRESHOLD = 0.72


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _slugify(value: str) -> str:
    lowered = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", (value or "").strip().lower()).strip("-")
    return lowered[:48] or "case"


def _canonical_case_key(source_kind: str, source_url: str | None, raw_text: str | None) -> str:
    parts = [source_kind.strip(), _normalize_text(source_url), _normalize_text(raw_text)]
    return "|".join(parts)


def _default_case_id(source_kind: str, source_url: str | None, raw_text: str | None, auto_reason: str = "") -> str:
    base = source_url or raw_text or source_kind
    digest = hashlib.sha1(_canonical_case_key(source_kind, source_url, raw_text).encode("utf-8")).hexdigest()[:8]
    prefix = "auto" if auto_reason else "manual"
    return f"{prefix}-{_slugify(base)}-{digest}"


def normalize_auto_reason(value: str | None) -> str:
    text = _normalize_text(value)
    if not text:
        return "unknown"
    if ":" in text:
        text = text.split(":", 1)[0].strip()
    return text[:80]


@dataclass
class RecognitionCase:
    case_id: str
    source_kind: str
    source_url: Optional[str] = None
    raw_text: Optional[str] = None
    platform_hint: Optional[str] = None
    provenance: str = "manual"
    labels: List[str] = field(default_factory=list)
    expect: EvalExpectation = field(default_factory=EvalExpectation)
    auto_reason: str = ""

    @classmethod
    def from_dict(cls, payload: Dict[str, Any], *, default_provenance: str = "manual") -> "RecognitionCase":
        source_kind = str(payload.get("source_kind", "")).strip()
        if source_kind not in SUPPORTED_SOURCE_KINDS:
            raise ValueError(f"unsupported source_kind: {source_kind}")
        source_url = payload.get("source_url")
        raw_text = payload.get("raw_text")
        auto_reason = normalize_auto_reason(payload.get("auto_reason", ""))
        case_id = str(payload.get("id") or payload.get("case_id") or "").strip()
        if not case_id:
            case_id = _default_case_id(source_kind, source_url, raw_text, auto_reason=auto_reason)
        labels = [str(item).strip() for item in payload.get("labels", []) if str(item).strip()]
        labels = list(dict.fromkeys(labels))
        provenance = str(payload.get("provenance") or default_provenance).strip() or default_provenance
        return cls(
            case_id=case_id,
            source_kind=source_kind,
            source_url=source_url,
            raw_text=raw_text,
            platform_hint=payload.get("platform_hint"),
            provenance=provenance,
            labels=labels,
            expect=EvalExpectation.from_dict(payload.get("expect", {})),
            auto_reason=auto_reason if provenance == "auto" else "",
        )

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "id": self.case_id,
            "source_kind": self.source_kind,
            "source_url": self.source_url,
            "raw_text": self.raw_text,
            "platform_hint": self.platform_hint,
            "provenance": self.provenance,
            "labels": list(self.labels),
            "expect": asdict(self.expect),
        }
        if self.auto_reason:
            payload["auto_reason"] = self.auto_reason
        return payload

    def canonical_key(self) -> str:
        return _canonical_case_key(self.source_kind, self.source_url, self.raw_text)

    def has_expectations(self) -> bool:
        expect = self.expect
        return bool(
            expect.required_keywords
            or expect.required_links
            or expect.required_projects
            or expect.required_skill_ids
            or expect.required_skills
            or expect.required_actions
            or expect.require_action_checklist
            or expect.forbidden_phrases
        )

    def to_eval_case(self) -> EvalCase:
        return EvalCase(
            case_id=self.case_id,
            source_kind=self.source_kind,
            source_url=self.source_url,
            raw_text=self.raw_text,
            platform_hint=self.platform_hint,
            expect=self.expect,
        )


def load_recognition_cases(path: str, *, default_provenance: str = "manual") -> List[RecognitionCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("recognition cases must be a JSON array")
    return [RecognitionCase.from_dict(item, default_provenance=default_provenance) for item in payload if isinstance(item, dict)]


def load_auto_case_inbox(path: str) -> List[RecognitionCase]:
    inbox_path = Path(path)
    if not inbox_path.exists():
        return []
    items: List[RecognitionCase] = []
    for raw_line in inbox_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            continue
        items.append(RecognitionCase.from_dict(payload, default_provenance="auto"))
    return items


def merge_recognition_cases(*case_groups: Iterable[RecognitionCase]) -> List[RecognitionCase]:
    merged: Dict[str, RecognitionCase] = {}
    for group in case_groups:
        for case in group:
            key = case.canonical_key()
            existing = merged.get(key)
            if existing is None:
                merged[key] = case
                continue
            if existing.provenance == "manual" and case.provenance != "manual":
                existing.labels = list(dict.fromkeys(existing.labels + case.labels))
                if not existing.auto_reason and case.auto_reason:
                    existing.auto_reason = case.auto_reason
                continue
            if case.provenance == "manual" and existing.provenance != "manual":
                case.labels = list(dict.fromkeys(case.labels + existing.labels))
                if not case.auto_reason and existing.auto_reason:
                    case.auto_reason = existing.auto_reason
                merged[key] = case
                continue
            existing.labels = list(dict.fromkeys(existing.labels + case.labels))
            if not existing.auto_reason and case.auto_reason:
                existing.auto_reason = case.auto_reason
    return sorted(merged.values(), key=lambda item: (item.provenance != "manual", item.case_id))


def append_auto_case(
    inbox_path: Path,
    *,
    source_kind: str,
    source_url: str | None,
    raw_text: str | None,
    platform_hint: str | None,
    auto_reason: str,
    labels: Optional[List[str]] = None,
) -> RecognitionCase:
    case = RecognitionCase(
        case_id=_default_case_id(source_kind, source_url, raw_text, auto_reason=auto_reason),
        source_kind=source_kind,
        source_url=source_url,
        raw_text=raw_text,
        platform_hint=platform_hint,
        provenance="auto",
        labels=list(dict.fromkeys([str(item).strip() for item in (labels or []) if str(item).strip()])),
        auto_reason=normalize_auto_reason(auto_reason),
    )
    existing = load_auto_case_inbox(str(inbox_path))
    if any(item.canonical_key() == case.canonical_key() and item.auto_reason == case.auto_reason for item in existing):
        return case
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    with inbox_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(case.to_dict(), ensure_ascii=False) + "\n")
    return case


def maybe_record_auto_case(
    inbox_path: Path | None,
    *,
    source_kind: str,
    source_url: str | None,
    raw_text: str | None,
    platform_hint: str | None,
    warnings: Optional[List[str]] = None,
    coverage: str | None = None,
    summary_quality_score: float | None = None,
    dry_run: bool = False,
    labels: Optional[List[str]] = None,
    extra_reason: str = "",
) -> bool:
    if inbox_path is None or dry_run or source_kind not in SUPPORTED_SOURCE_KINDS:
        return False
    reason = normalize_auto_reason(extra_reason) if extra_reason else ""
    warning_list = [str(item).strip() for item in (warnings or []) if str(item).strip()]
    if not reason and warning_list:
        reason = normalize_auto_reason(warning_list[0])
    if not reason and coverage == "partial":
        reason = "coverage_partial"
    if not reason and summary_quality_score is not None and float(summary_quality_score) < AUTO_CASE_SCORE_THRESHOLD:
        reason = "summary_quality_low"
    if not reason:
        return False
    append_auto_case(
        inbox_path,
        source_kind=source_kind,
        source_url=source_url,
        raw_text=raw_text,
        platform_hint=platform_hint,
        auto_reason=reason,
        labels=labels,
    )
    return True

