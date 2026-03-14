"""Material-only note rendering helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Protocol
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from .config import SummarizerConfig
from .models import EvidenceBundle, SummaryResult


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "learning_note_system.md"
FALLBACK_SYSTEM_PROMPT = "只根据给定物料总结。不要编造。直接返回 Markdown 正文。"
NOTE_USER_PROMPT_TEMPLATE = "下面是物料 JSON：\n\n```json\n{materials_json}\n```"


class NoteRenderEngine(Protocol):
    def render(self, materials: Dict[str, Any]) -> str:
        ...


def load_note_system_prompt() -> str:
    if PROMPT_PATH.exists():
        text = PROMPT_PATH.read_text(encoding="utf-8").strip()
        if text:
            return text
    return FALLBACK_SYSTEM_PROMPT


def build_note_materials(
    *,
    summary: SummaryResult,
    evidence: EvidenceBundle,
    structure_map: str,
    topic_links: List[str],
    entity_links: List[str],
    keyword_links: List[str],
    skipped_topics: List[str],
    canonical_source_url: str,
) -> Dict[str, Any]:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
    structured_document = metadata.get("structured_document", {}) if isinstance(metadata.get("structured_document"), dict) else {}
    warnings = metadata.get("fetch_warnings", []) if isinstance(metadata.get("fetch_warnings"), list) else []
    analyzer_warnings = metadata.get("analyzer_warnings", []) if isinstance(metadata.get("analyzer_warnings"), list) else []
    steps = metadata.get("steps", []) if isinstance(metadata.get("steps"), list) else []
    step_items = metadata.get("step_items", []) if isinstance(metadata.get("step_items"), list) else []
    commands = list(signals.get("commands", [])) if isinstance(signals.get("commands"), list) else []
    video_story_blocks = metadata.get("video_story_blocks", []) if isinstance(metadata.get("video_story_blocks"), list) else []
    viewer_feedback = metadata.get("viewer_feedback", []) if isinstance(metadata.get("viewer_feedback"), list) else []
    viewer_feedback_capture = (
        metadata.get("viewer_feedback_capture", {})
        if isinstance(metadata.get("viewer_feedback_capture"), dict)
        else {}
    )
    user_guidance = str(metadata.get("user_guidance", "")).strip() if isinstance(metadata, dict) else ""
    merged_warnings = list(warnings) + [item for item in analyzer_warnings if item not in warnings]
    warning_summary = _summarize_warnings(merged_warnings)
    capture_status = _build_capture_status(evidence, merged_warnings)
    return {
        "title": summary.title,
        "source": {
            "source_kind": evidence.source_kind,
            "platform": evidence.platform_hint or "",
            "source_url": canonical_source_url,
            "content_profile": metadata.get("content_profile", {}).get("kind", "") if isinstance(metadata.get("content_profile"), dict) else "",
            "evidence_type": evidence.evidence_type,
        },
        "structured_document": structured_document,
        "summary": summary.to_dict(),
        "evidence": {
            "title": evidence.title,
            "coverage": evidence.coverage,
            "text": evidence.text,
            "transcript": evidence.transcript or "",
            "evidence_quotes": list(summary.evidence_quotes),
        },
        "fragments": {
            "commands": commands,
            "steps": steps,
            "step_items": step_items,
            "signals": signals,
            "video_story_blocks": video_story_blocks,
            "viewer_feedback": viewer_feedback,
            "viewer_feedback_capture": viewer_feedback_capture,
        },
        "warnings": warning_summary,
        "warning_summary": warning_summary,
        "coverage": summary.coverage,
        "context": {
            "topic_links": list(topic_links),
            "entity_links": list(entity_links),
            "keyword_links": list(keyword_links),
            "skipped_topics": list(skipped_topics),
            "structure_map": structure_map,
            "user_guidance": user_guidance,
            "capture_status": capture_status,
        },
    }


def _summarize_warnings(warnings: List[str]) -> List[str]:
    summaries: list[str] = []
    for warning in warnings:
        text = str(warning or "").strip()
        if not text:
            continue
        lowered = text.lower()
        summary = ""
        if text.startswith("video_audio_failed"):
            summary = "未能拿到音频轨。"
        elif text.startswith("video_keyframes_failed"):
            summary = "未能拿到视频关键帧。"
        elif text.startswith("video_page_snapshot_failed"):
            summary = "未能拿到页面补充文本。"
        elif text.startswith("video_html_fallback_failed"):
            summary = "页面兜底文本也没有成功拿到。"
        elif text.startswith("video_subtitle_failed"):
            summary = "未能拿到字幕轨。"
        elif text.startswith("browser_snapshot_failed"):
            summary = "浏览器快照抓取失败。"
        elif "unsupported url" in lowered:
            summary = "当前分享链接形态不支持直接提取。"
        elif "no video formats found" in lowered:
            summary = "当前没有拿到可用的视频流。"
        elif "当前笔记暂时无法浏览" in text or "页面不见了" in text:
            summary = "当前页面不可见，内容拿不到。"
        elif "request was rejected" in lowered or "安全限制" in text:
            summary = "平台侧有访问限制。"
        if summary and summary not in summaries:
            summaries.append(summary)
        if len(summaries) >= 4:
            break
    return summaries


def _build_capture_status(evidence: EvidenceBundle, warnings: List[str]) -> Dict[str, Any]:
    if evidence.source_kind != "video_url":
        return {"kind": "normal", "summary": ""}
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    tracks = metadata.get("tracks", {}) if isinstance(metadata.get("tracks"), dict) else {}
    warning_summary = _summarize_warnings(warnings)
    has_any_track = any(
        bool(tracks.get(key))
        for key in ["has_subtitle", "has_transcript", "has_keyframes", "has_keyframe_ocr"]
    )
    title = str(evidence.title or "").strip()
    if not has_any_track:
        if "页面不见了" in title or "无法浏览" in title:
            return {
                "kind": "video_extract_blocked",
                "summary": "这条内容当前拿不到有效视频正文，继续看这版结果意义不大。",
            }
        if warning_summary:
            return {
                "kind": "video_extract_blocked",
                "summary": "这条视频当前没有拿到可用的正文或画面信息，先别在这条上继续投入时间。",
            }
    if has_any_track and warning_summary:
        return {
            "kind": "video_partial",
            "summary": "这条视频拿到了部分证据，结论可以先参考，但别过度相信细节。",
        }
    return {"kind": "normal", "summary": ""}


def build_note_user_prompt(materials: Dict[str, Any]) -> str:
    return NOTE_USER_PROMPT_TEMPLATE.format(
        materials_json=json.dumps(materials, ensure_ascii=False, indent=2)
    )


def save_materials_file(materials: Dict[str, Any], output_dir: Path, note_title: str) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = hashlib.sha1(note_title.encode("utf-8")).hexdigest()[:12]
    path = output_dir / f"{slug}.json"
    path.write_text(json.dumps(materials, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


class OpenAICompatibleNoteRenderer:
    def __init__(self, config: SummarizerConfig) -> None:
        self.config = config
        self.system_prompt = load_note_system_prompt()

    def render(self, materials: Dict[str, Any]) -> str:
        payload = {
            "model": self.config.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": build_note_user_prompt(materials)},
            ],
        }
        req = urlrequest.Request(
            url=f"{self.config.api_base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"note renderer request failed: {exc}") from exc
        try:
            content = str(body["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"unexpected note renderer response: {body}") from exc
        if not content:
            raise RuntimeError("note renderer returned empty content")
        return content
