"""OpenAI Responses API client for structured URL understanding."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from ..config import AppConfig
from .models import CollectedImage, CollectedVideo, ExtractedContent, StructuredDocument


PROMPT = """You are a URL understanding engine.

Read the provided webpage text, tables, screenshot, downloaded images, and sampled video frames.
Return strict JSON with the following keys only:
title, summary, sections, images, videos, tables

Rules:
- Write in Chinese unless the input clearly requires another language.
- summary must describe what the page is about using the extracted content, not just the title.
- summary should be concise and content-first; avoid repeating obvious site chrome, repository stats, language pickers, or table-of-contents noise.
- sections should capture the main topical structure of the page.
- prefer real content sections over navigation, sidebars, language switchers, and loading/error banners.
- if the page is a GitHub repository page, prioritize README or visible documentation content over repository chrome.
- if the page is a documentation landing page with a long table of contents, summarize the actual intro/body first and avoid letting the TOC dominate the output.
- images should preserve src/alt/caption/context when available.
- videos should preserve src/poster/provider/duration_seconds/frame_summaries.
- tables should preserve caption/headers/rows.
- Do not include local file paths.
- Do not invent data that is not supported by the provided evidence.
"""


def _json_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "summary", "sections", "images", "videos", "tables"],
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["heading", "level", "content"],
                    "properties": {
                        "heading": {"type": ["string", "null"]},
                        "level": {"type": ["integer", "null"]},
                        "content": {"type": "string"},
                    },
                },
            },
            "images": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["src", "alt", "caption", "context"],
                    "properties": {
                        "src": {"type": "string"},
                        "alt": {"type": ["string", "null"]},
                        "caption": {"type": ["string", "null"]},
                        "context": {"type": ["string", "null"]},
                    },
                },
            },
            "videos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["src", "poster", "provider", "duration_seconds", "frame_summaries"],
                    "properties": {
                        "src": {"type": "string"},
                        "poster": {"type": ["string", "null"]},
                        "provider": {"type": ["string", "null"]},
                        "duration_seconds": {"type": ["number", "null"]},
                        "frame_summaries": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "tables": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["caption", "headers", "rows"],
                    "properties": {
                        "caption": {"type": ["string", "null"]},
                        "headers": {"type": "array", "items": {"type": "string"}},
                        "rows": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    }


def _path_to_data_url(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _iter_media_parts(paths: Iterable[Path]) -> Iterable[Dict[str, Any]]:
    for path in paths:
        if not path.exists():
            continue
        yield {"type": "input_image", "image_url": _path_to_data_url(path)}


def _text_payload(extracted: ExtractedContent, requested_output_lang: str) -> str:
    payload = {
        "requested_output_lang": requested_output_lang,
        "title": extracted.title,
        "main_text": extracted.main_text,
        "sections": [item.to_dict() for item in extracted.sections],
        "images": [item.result.to_dict() for item in extracted.images],
        "videos": [item.result.to_dict() for item in extracted.videos],
        "tables": [item.to_dict() for item in extracted.tables],
    }
    return json.dumps(payload, ensure_ascii=False)


def _extract_response_text(body: Dict[str, Any]) -> str:
    output_text = body.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    output = body.get("output", [])
    if not isinstance(output, list):
        raise RuntimeError("unexpected responses api payload")
    texts: List[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content", [])
        if not isinstance(content, list):
            continue
        for chunk in content:
            if not isinstance(chunk, dict):
                continue
            text = chunk.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
    merged = "\n".join(texts).strip()
    if not merged:
        raise RuntimeError("responses api returned no text output")
    return merged


def _normalize_for_compare(value: str) -> str:
    return "".join(ch.lower() for ch in value if not ch.isspace())


def _validate_document_quality(document: StructuredDocument, extracted: ExtractedContent) -> List[str]:
    issues: List[str] = []
    if not document.title.strip():
        issues.append("missing_title")
    if not document.summary.strip():
        issues.append("missing_summary")
    if document.summary and _normalize_for_compare(document.summary) == _normalize_for_compare(document.title):
        issues.append("summary_repeats_title")
    if extracted.main_text.strip() and not any(section.content.strip() for section in document.sections):
        issues.append("missing_section_content")
    if len(extracted.main_text.strip()) >= 200 and document.sections and not any(len(section.content.strip()) >= 20 for section in document.sections):
        issues.append("sections_too_thin")
    if extracted.tables and len(document.tables) != len(extracted.tables):
        issues.append("table_shape_mismatch")
    if extracted.videos and len(document.videos) != len(extracted.videos):
        issues.append("video_shape_mismatch")
    return issues


class OpenAIResponsesClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def generate_document(
        self,
        extracted: ExtractedContent,
        requested_output_lang: str,
        screenshot_path: Optional[Path],
    ) -> StructuredDocument:
        errors: List[str] = []
        for model in [self.config.analysis.model, self.config.analysis.fallback_model]:
            try:
                raw = self._perform_request(
                    model=model,
                    extracted=extracted,
                    requested_output_lang=requested_output_lang,
                    screenshot_path=screenshot_path,
                )
                document = StructuredDocument.from_dict(json.loads(raw))
                issues = _validate_document_quality(document, extracted)
                if issues:
                    raise RuntimeError("quality_validation_failed:" + ",".join(issues))
                return document
            except Exception as exc:
                errors.append(f"{model}:{exc}")
        raise RuntimeError("responses api generation failed: " + " | ".join(errors))

    def _perform_request(
        self,
        model: str,
        extracted: ExtractedContent,
        requested_output_lang: str,
        screenshot_path: Optional[Path],
    ) -> str:
        content: List[Dict[str, Any]] = [
            {"type": "input_text", "text": _text_payload(extracted, requested_output_lang)}
        ]
        media_paths: List[Path] = []
        if screenshot_path is not None:
            media_paths.append(screenshot_path)
        media_paths.extend([item.local_path for item in extracted.images if item.local_path is not None])
        for video in extracted.videos:
            media_paths.extend(video.frame_paths)
        content.extend(_iter_media_parts(media_paths))

        payload = {
            "model": model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": PROMPT}]},
                {"role": "user", "content": content},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "structured_document",
                    "schema": _json_schema(),
                    "strict": True,
                }
            },
        }
        req = urlrequest.Request(
            url=f"{self.config.summarizer.api_base_url.rstrip('/')}/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.summarizer.api_key}",
            },
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=self.config.summarizer.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"responses request failed: {exc}") from exc
        return _extract_response_text(body)
