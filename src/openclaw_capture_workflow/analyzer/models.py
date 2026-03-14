"""Data models for the URL understanding analyzer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SectionResult:
    heading: Optional[str]
    level: Optional[int]
    content: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ImageResult:
    src: str
    alt: Optional[str] = None
    caption: Optional[str] = None
    context: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VideoResult:
    src: str
    poster: Optional[str] = None
    provider: Optional[str] = None
    duration_seconds: Optional[float] = None
    frame_summaries: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TableResult:
    caption: Optional[str] = None
    headers: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StructuredDocument:
    title: str
    summary: str
    sections: List[SectionResult] = field(default_factory=list)
    images: List[ImageResult] = field(default_factory=list)
    videos: List[VideoResult] = field(default_factory=list)
    tables: List[TableResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "sections": [item.to_dict() for item in self.sections],
            "images": [item.to_dict() for item in self.images],
            "videos": [item.to_dict() for item in self.videos],
            "tables": [item.to_dict() for item in self.tables],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "StructuredDocument":
        if not isinstance(payload, dict):
            raise TypeError("structured document payload must be a dict")
        title = str(payload.get("title", "")).strip()
        summary = str(payload.get("summary", "")).strip()
        if not title:
            raise ValueError("structured document missing title")
        if not summary:
            raise ValueError("structured document missing summary")

        def _parse_sections(values: Any) -> List[SectionResult]:
            items: List[SectionResult] = []
            if not isinstance(values, list):
                return items
            for item in values:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content", "")).strip()
                if not content:
                    continue
                heading = item.get("heading")
                level = item.get("level")
                items.append(
                    SectionResult(
                        heading=str(heading).strip() if heading is not None and str(heading).strip() else None,
                        level=int(level) if isinstance(level, int) else None,
                        content=content,
                    )
                )
            return items

        def _parse_images(values: Any) -> List[ImageResult]:
            items: List[ImageResult] = []
            if not isinstance(values, list):
                return items
            for item in values:
                if not isinstance(item, dict):
                    continue
                src = str(item.get("src", "")).strip()
                if not src:
                    continue
                items.append(
                    ImageResult(
                        src=src,
                        alt=str(item.get("alt", "")).strip() or None,
                        caption=str(item.get("caption", "")).strip() or None,
                        context=str(item.get("context", "")).strip() or None,
                    )
                )
            return items

        def _parse_videos(values: Any) -> List[VideoResult]:
            items: List[VideoResult] = []
            if not isinstance(values, list):
                return items
            for item in values:
                if not isinstance(item, dict):
                    continue
                src = str(item.get("src", "")).strip()
                if not src:
                    continue
                duration = item.get("duration_seconds")
                frame_summaries = item.get("frame_summaries", [])
                items.append(
                    VideoResult(
                        src=src,
                        poster=str(item.get("poster", "")).strip() or None,
                        provider=str(item.get("provider", "")).strip() or None,
                        duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
                        frame_summaries=[str(entry).strip() for entry in frame_summaries if str(entry).strip()]
                        if isinstance(frame_summaries, list)
                        else [],
                    )
                )
            return items

        def _parse_tables(values: Any) -> List[TableResult]:
            items: List[TableResult] = []
            if not isinstance(values, list):
                return items
            for item in values:
                if not isinstance(item, dict):
                    continue
                headers = item.get("headers", [])
                rows = item.get("rows", [])
                items.append(
                    TableResult(
                        caption=str(item.get("caption", "")).strip() or None,
                        headers=[str(entry).strip() for entry in headers if str(entry).strip()]
                        if isinstance(headers, list)
                        else [],
                        rows=[
                            [str(cell).strip() for cell in row]
                            for row in rows
                            if isinstance(row, list)
                        ]
                        if isinstance(rows, list)
                        else [],
                    )
                )
            return items

        return cls(
            title=title,
            summary=summary,
            sections=_parse_sections(payload.get("sections", [])),
            images=_parse_images(payload.get("images", [])),
            videos=_parse_videos(payload.get("videos", [])),
            tables=_parse_tables(payload.get("tables", [])),
        )


@dataclass
class RenderResult:
    requested_url: str
    final_url: str
    title: str
    html: str
    screenshot_path: Optional[Path] = None
    text_hint: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectedImage:
    result: ImageResult
    local_path: Optional[Path] = None


@dataclass
class CollectedVideo:
    result: VideoResult
    local_video_path: Optional[Path] = None
    frame_paths: List[Path] = field(default_factory=list)


@dataclass
class ExtractedContent:
    title: str
    main_text: str
    sections: List[SectionResult] = field(default_factory=list)
    images: List[CollectedImage] = field(default_factory=list)
    videos: List[CollectedVideo] = field(default_factory=list)
    tables: List[TableResult] = field(default_factory=list)


@dataclass
class AnalysisOutcome:
    document: StructuredDocument
    warnings: List[str] = field(default_factory=list)

