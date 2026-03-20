"""Data models for ingestion, evidence, summaries, and job state."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import json
import uuid


def utc_now() -> str:
    """Return an ISO8601 UTC timestamp with second precision."""
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


JOB_PHASES = ("extract", "summarize", "write_note", "notify")


def default_phase_status() -> Dict[str, str]:
    return {phase: "pending" for phase in JOB_PHASES}


def _json_list_field(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return [value]


@dataclass
class IngestRequest:
    chat_id: str
    reply_to_message_id: Optional[str]
    request_id: str
    source_kind: str
    source_url: Optional[str] = None
    raw_text: Optional[str] = None
    image_refs: List[str] = field(default_factory=list)
    platform_hint: Optional[str] = None
    requested_output_lang: str = "zh-CN"
    dry_run: bool = False
    video_probe_seconds: Optional[int] = None
    force_full_video: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IngestRequest":
        payload = dict(data)
        payload.setdefault("request_id", str(uuid.uuid4()))
        payload.setdefault("reply_to_message_id", None)
        payload.setdefault("source_url", None)
        payload.setdefault("raw_text", None)
        payload.setdefault("image_refs", [])
        payload.setdefault("platform_hint", None)
        payload.setdefault("requested_output_lang", "zh-CN")
        payload.setdefault("dry_run", False)
        payload.setdefault("video_probe_seconds", None)
        payload.setdefault("force_full_video", False)
        return cls(**payload)


@dataclass
class EvidenceItem:
    modality: str
    source: str
    provider: str
    text: str
    timestamp_start: Optional[float] = None
    timestamp_end: Optional[float] = None
    confidence: float = 0.0
    artifact_ref: Optional[str] = None
    is_primary: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CaptureRecord:
    status: str = "empty"
    provider: str = ""
    reason: str = ""
    artifact_refs: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_CAPTURE_SLOTS = (
    "page_metadata",
    "subtitle",
    "asr",
    "keyframes",
    "keyframe_ocr",
    "github_repo",
    "web_html",
    "browser_render",
    "comments",
)


@dataclass
class CaptureManifest:
    items: Dict[str, CaptureRecord] = field(default_factory=dict)

    def ensure_defaults(self) -> None:
        for name in DEFAULT_CAPTURE_SLOTS:
            if name not in self.items or not isinstance(self.items[name], CaptureRecord):
                self.items[name] = self.items.get(name) if isinstance(self.items.get(name), CaptureRecord) else CaptureRecord()

    def to_dict(self) -> Dict[str, Any]:
        self.ensure_defaults()
        return {name: record.to_dict() for name, record in self.items.items()}


@dataclass
class EvidenceBundle:
    source_kind: str
    source_url: Optional[str]
    platform_hint: Optional[str]
    title: Optional[str]
    text: str
    evidence_type: str
    coverage: str
    transcript: Optional[str] = None
    keyframes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    merged_text: str = ""
    evidence_items: List[EvidenceItem] = field(default_factory=list)
    capture_manifest: CaptureManifest = field(default_factory=CaptureManifest)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["evidence_items"] = [item.to_dict() for item in self.evidence_items]
        payload["capture_manifest"] = self.capture_manifest.to_dict()
        payload["merged_text"] = self.merged_text or self.text
        return payload


@dataclass
class SummaryResult:
    title: str
    primary_topic: str
    secondary_topics: List[str]
    entities: List[str]
    conclusion: str
    bullets: List[str]
    evidence_quotes: List[str]
    coverage: str
    confidence: str
    note_tags: List[str]
    follow_up_actions: List[str]
    timeliness: str = "medium"
    effectiveness: str = "medium"
    recommendation_level: str = "optional"
    reader_judgment: str = ""
    outcome: str = "summarized"
    evidence_basis: List[str] = field(default_factory=list)
    timeline_sections: List[Dict[str, Any]] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)
    refusal_reason: str = ""
    finance_matrix: List[Dict[str, Any]] = field(default_factory=list)
    finance_snapshot: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, text: str) -> "SummaryResult":
        data = json.loads(text)
        return cls(
            title=data["title"],
            primary_topic=data["primary_topic"],
            secondary_topics=[str(item) for item in _json_list_field(data.get("secondary_topics", [])) if str(item).strip()],
            entities=[str(item) for item in _json_list_field(data.get("entities", [])) if str(item).strip()],
            conclusion=data["conclusion"],
            bullets=[str(item) for item in _json_list_field(data.get("bullets", [])) if str(item).strip()],
            evidence_quotes=[str(item) for item in _json_list_field(data.get("evidence_quotes", [])) if str(item).strip()],
            coverage=data.get("coverage", "partial"),
            confidence=data.get("confidence", "medium"),
            note_tags=[str(item) for item in _json_list_field(data.get("note_tags", [])) if str(item).strip()],
            follow_up_actions=[str(item) for item in _json_list_field(data.get("follow_up_actions", [])) if str(item).strip()],
            timeliness=data.get("timeliness", "medium"),
            effectiveness=data.get("effectiveness", "medium"),
            recommendation_level=data.get("recommendation_level", "optional"),
            reader_judgment=data.get("reader_judgment", ""),
            outcome=data.get("outcome", "summarized"),
            evidence_basis=[str(item) for item in _json_list_field(data.get("evidence_basis", [])) if str(item).strip()],
            timeline_sections=[item for item in _json_list_field(data.get("timeline_sections", [])) if isinstance(item, dict)],
            uncertainties=[str(item) for item in _json_list_field(data.get("uncertainties", [])) if str(item).strip()],
            refusal_reason=data.get("refusal_reason", ""),
            finance_matrix=[item for item in _json_list_field(data.get("finance_matrix", [])) if isinstance(item, dict)],
            finance_snapshot=data.get("finance_snapshot", {}) if isinstance(data.get("finance_snapshot", {}), dict) else {},
        )


@dataclass
class JobRecord:
    job_id: str
    status: str
    created_at: str
    updated_at: str
    request: Dict[str, Any]
    message: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    phase_status: Dict[str, str] = field(default_factory=default_phase_status)
    notification: Dict[str, Any] = field(
        default_factory=lambda: {"attempted": False, "ok": None, "error": None}
    )

    @classmethod
    def queued(cls, request: IngestRequest) -> "JobRecord":
        now = utc_now()
        return cls(
            job_id=request.request_id,
            status="received",
            created_at=now,
            updated_at=now,
            request=asdict(request),
            message="queued",
        )

    def ensure_tracking_fields(self) -> None:
        if not isinstance(self.phase_status, dict):
            self.phase_status = default_phase_status()
        for phase in JOB_PHASES:
            self.phase_status.setdefault(phase, "pending")
        if not isinstance(self.notification, dict):
            self.notification = {"attempted": False, "ok": None, "error": None}
        self.notification.setdefault("attempted", False)
        self.notification.setdefault("ok", None)
        self.notification.setdefault("error", None)
        if not isinstance(self.warnings, list):
            self.warnings = []

    def set_phase(self, phase: str, status: str) -> None:
        self.ensure_tracking_fields()
        if phase in self.phase_status:
            self.phase_status[phase] = status
            self.updated_at = utc_now()

    def add_warning(self, warning: str) -> None:
        self.ensure_tracking_fields()
        if warning and warning not in self.warnings:
            self.warnings.append(warning)
            self.updated_at = utc_now()

    def mark(self, status: str, *, message: str = "", result: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> None:
        self.status = status
        self.updated_at = utc_now()
        if message:
            self.message = message
        if result is not None:
            self.result = result
        if error is not None:
            self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
