"""Background processing pipeline."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import queue
import re
import shutil
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.parse import quote

from .config import AppConfig, SummarizerConfig
from .content_profile import build_signal_requirements, infer_content_profile, iter_required_signal_entries
from .evidence_utils import build_evidence_digest
from .extractor import EvidenceExtractor
from .models import IngestRequest, JobRecord, SummaryResult
from .obsidian import ObsidianWriter
from .note_renderer import OpenAICompatibleNoteRenderer
from .quality_gate import (
    build_model_unavailable_summary,
    build_refusal_summary,
    build_theme_only_summary,
    evaluate_quality_gate,
)
from .storage import JobStore
from .summarizer import (
    OpenAICompatibleSummarizer,
    SummaryEngine,
    PROMPT_VERSION,
    _signal_priority_bullets,
    _validate_and_normalize_summary,
)
from .telegram import TelegramNotifier
from .video_experiment_summarizer import AiHubMixGeminiSummarizer, EvidenceDrivenVideoSummarizer
from .video_summary_score import score_video_summary
from .video_story_blocks import get_qualified_video_story_blocks, get_viewer_feedback
from .video_truth_eval import compute_retained_outline_count


FALLBACK_SUMMARY_VERSION = "20260315-video-story-v1"
_RESOURCE_BULLET_LABELS = {"GitHub地址", "视频链接", "关键链接", "仓库地址", "文档链接", "来源链接", "链接"}
_BOUNDARY_BULLET_LABELS = {"适用边界", "常见错误", "验证边界", "风险", "边界", "限制"}
_HARD_FACT_BULLET_LABELS = {
    "项目名称",
    "项目仓库",
    "技能名",
    "技能ID",
    "安装方法",
    "关键命令",
    "前置条件",
    "验证动作",
    "使用方式",
    "核心用途",
    "流程要点",
    "平台支持",
    "适用边界",
    "常见错误",
    "主题",
}
_PSEUDO_SUMMARY_TOKENS = [
    "已提取核心事实",
    "帮助快速",
    "内容完整",
    "覆盖全面",
    "适合作为参考",
    "适合先留档",
    "值得继续",
    "有信息价值",
    "值得回看",
    "值得留档",
    "可作为背景资料",
]


def _has_sufficient_evidence_text(
    source_kind: str,
    text: str,
    source_url: str | None = None,
    metadata: dict[str, Any] | None = None,
    gate_config=None,
) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return False
    min_chars_signal = 40
    min_chars_media = 60
    min_chars_general = 80
    allow_pasted_short = True
    if gate_config is not None:
        min_chars_signal = int(getattr(gate_config, "min_chars_signal_rich_short", min_chars_signal))
        min_chars_media = int(getattr(gate_config, "min_chars_media", min_chars_media))
        min_chars_general = int(getattr(gate_config, "min_chars_general", min_chars_general))
        allow_pasted_short = bool(getattr(gate_config, "allow_pasted_text_without_min", allow_pasted_short))
    if source_kind == "pasted_text" and allow_pasted_short:
        return True
    if re.fullmatch(r"https?://\S+", cleaned):
        return False
    if source_url and cleaned == source_url.strip():
        return False
    lowered = cleaned.lower()
    if lowered.startswith(
        (
            "[fetch error]",
            "[extractor error]",
            "[github fetch error]",
            "[github extractor error]",
            "[browser fetch error]",
        )
    ):
        return False
    signals = {}
    if isinstance(metadata, dict):
        raw_signals = metadata.get("signals", {})
        if isinstance(raw_signals, dict):
            signals = raw_signals
        evidence_sources = metadata.get("evidence_sources", [])
        if isinstance(evidence_sources, list) and "web_blocked_notice" in evidence_sources:
            return True
    has_signal = any(
        bool(signals.get(key))
        for key in ["skills", "skill_ids", "commands", "links", "supported_platforms", "prerequisites", "validation_actions", "boundaries"]
    )
    if has_signal and len(cleaned) >= min_chars_signal:
        return True
    if source_kind in {"image", "video_url", "mixed"} and len(cleaned) >= min_chars_media:
        return True
    if len(cleaned) < min_chars_general:
        return False
    return True


def _is_web_blocked_notice_evidence(evidence) -> bool:
    if evidence.source_kind == "video_url":
        return False
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    evidence_sources = metadata.get("evidence_sources", [])
    return isinstance(evidence_sources, list) and "web_blocked_notice" in evidence_sources


def _build_web_blocked_notice_summary(evidence: EvidenceBundle) -> SummaryResult:
    title = re.sub(r"\s+", " ", str(evidence.title or "页面不可见").strip()) or "页面不可见"
    source_url = re.sub(r"\s+", " ", str(evidence.source_url or "").strip())
    bullets = [
        "当前拿不到正文，不能把这版结果当成正常内容总结。",
        "原因更像链接失效、页面不可见或平台访问限制。",
        "当前不建议继续基于这版结果投入时间或判断内容。",
    ]
    if source_url:
        bullets.append(f"来源链接: {source_url}")
    return SummaryResult(
        title=title,
        primary_topic="页面访问受限",
        secondary_topics=[],
        entities=[],
        conclusion="当前页面不可见或正文未返回，这版结果不能作为内容总结使用。",
        bullets=bullets,
        evidence_quotes=[],
        coverage="partial",
        confidence="low",
        note_tags=["web_blocked_notice"],
        follow_up_actions=["如果后续仍需要这条内容，再补正常页面、登录态或浏览环境后重试。"],
        timeliness="low",
        effectiveness="low",
        recommendation_level="skip",
        reader_judgment="当前只能确认链接受限，不能可靠还原正文内容。",
        outcome="partial",
        evidence_basis=["web_blocked_notice"],
        uncertainties=["页面或平台限制导致正文缺失。"],
    )


def _estimate_tokens(text: str) -> int:
    # Lightweight estimate that works for mixed Chinese/English text.
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return 0
    return max(1, int(len(compact) * 1.05))


def _normalize_source_url_for_cache(source_url: str | None) -> str:
    if not source_url:
        return ""
    try:
        parsed = urlsplit(source_url.strip())
    except ValueError:
        return source_url.strip()
    if not parsed.scheme or not parsed.netloc:
        return source_url.strip()
    query_items = parse_qsl(parsed.query, keep_blank_values=False)
    ignored_keys = {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "share_id",
        "share_source",
        "share_medium",
        "share_session_id",
        "share_from",
        "share_tag",
        "apptime",
        "shareRedId",
        "author_share",
        "xsec_source",
        "xsec_token",
        "spm_id_from",
        "from_spmid",
        "timestamp",
        "unique_k",
        "mid",
        "buvid",
        "vd_source",
    }
    filtered_items = [(k, v) for k, v in query_items if k not in ignored_keys and not k.startswith("utm_")]
    normalized_query = urlencode(filtered_items, doseq=True)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", normalized_query, ""))


def _evidence_fingerprint(evidence) -> str:
    base_url = _normalize_source_url_for_cache(evidence.source_url)
    text = re.sub(r"\s+", " ", (evidence.text or "").strip())
    transcript = re.sub(r"\s+", " ", (evidence.transcript or "").strip())
    tracks = evidence.metadata.get("tracks", {}) if isinstance(evidence.metadata, dict) else {}
    evidence_items = [
        {
            "source": getattr(item, "source", ""),
            "text": re.sub(r"\s+", " ", getattr(item, "text", "").strip())[:500],
            "start": getattr(item, "timestamp_start", None),
            "end": getattr(item, "timestamp_end", None),
            "primary": bool(getattr(item, "is_primary", False)),
        }
        for item in getattr(evidence, "evidence_items", [])[:120]
    ]
    payload = {
        "source_url": base_url,
        "evidence_type": evidence.evidence_type,
        "coverage": evidence.coverage,
        "text": text[:12000],
        "transcript": transcript[:12000],
        "tracks": tracks,
        "evidence_items": evidence_items,
        "capture_manifest": evidence.capture_manifest.to_dict() if hasattr(evidence, "capture_manifest") else {},
        "prompt_version": PROMPT_VERSION,
        "fallback_summary_version": FALLBACK_SUMMARY_VERSION,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_utc_timestamp(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _video_gate_reasons(evidence, config: AppConfig) -> list[str]:
    if evidence.source_kind != "video_url" or not config.video_accuracy.enabled:
        return []
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    tracks = metadata.get("tracks", {}) if isinstance(metadata.get("tracks"), dict) else {}
    has_subtitle = bool(tracks.get("has_subtitle"))
    has_transcript = bool(tracks.get("has_transcript"))
    has_keyframes = bool(tracks.get("has_keyframes"))
    has_keyframe_ocr = bool(tracks.get("has_keyframe_ocr"))
    reasons: list[str] = []
    if config.video_accuracy.require_speech_track and not (has_subtitle or has_transcript):
        reasons.append("missing speech track (subtitle/transcript)")
    if config.video_accuracy.require_visual_track and not (has_keyframe_ocr or has_keyframes):
        reasons.append("missing visual track (keyframes/ocr)")
    if len((evidence.text or "").strip()) < config.video_accuracy.min_text_chars:
        reasons.append(f"evidence text too short (<{config.video_accuracy.min_text_chars} chars)")
    return reasons


def _video_track_score(evidence) -> int:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    tracks = metadata.get("tracks", {}) if isinstance(metadata.get("tracks"), dict) else {}
    score = 0
    if tracks.get("has_subtitle"):
        score += 2
    if tracks.get("has_transcript"):
        score += 3
    if tracks.get("has_keyframes"):
        score += 1
    if tracks.get("has_keyframe_ocr"):
        score += 1
    text_len = len((evidence.text or "").strip())
    if text_len >= 400:
        score += 2
    elif text_len >= 180:
        score += 1
    return score


def _is_video_recovery_better(
    current_evidence,
    recovered_evidence,
    current_reasons: list[str],
    recovered_reasons: list[str],
    min_char_gain: int,
) -> bool:
    if len(recovered_reasons) < len(current_reasons):
        return True
    current_score = _video_track_score(current_evidence)
    recovered_score = _video_track_score(recovered_evidence)
    if recovered_score > current_score:
        return True
    current_len = len((current_evidence.text or "").strip())
    recovered_len = len((recovered_evidence.text or "").strip())
    if recovered_len >= current_len + max(0, int(min_char_gain)):
        return True
    return False


def _estimate_video_cost_rmb(evidence, config: AppConfig) -> dict[str, float]:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    tracks = metadata.get("tracks", {}) if isinstance(metadata.get("tracks"), dict) else {}
    has_subtitle = bool(tracks.get("has_subtitle"))
    has_transcript = bool(tracks.get("has_transcript"))
    duration_seconds = metadata.get("video_duration_seconds")
    try:
        duration_seconds = float(duration_seconds)
    except (TypeError, ValueError):
        duration_seconds = config.video_accuracy.default_duration_minutes * 60.0
    duration_minutes = max(0.0, duration_seconds / 60.0)

    # If transcript was generated, assume ASR API cost; subtitles are treated as free extraction.
    asr_usd = duration_minutes * config.video_accuracy.asr_usd_per_min if has_transcript else 0.0
    if has_subtitle and not has_transcript:
        asr_usd = 0.0
    input_tokens = _estimate_tokens(evidence.text)
    output_tokens = int(config.video_accuracy.expected_summary_output_tokens)
    summary_usd = (
        input_tokens / 1_000_000.0 * config.video_accuracy.summary_input_usd_per_million
        + output_tokens / 1_000_000.0 * config.video_accuracy.summary_output_usd_per_million
    )
    total_usd = asr_usd + summary_usd
    total_rmb = total_usd * config.video_accuracy.usd_cny
    return {
        "duration_minutes": round(duration_minutes, 3),
        "asr_usd": round(asr_usd, 6),
        "summary_usd": round(summary_usd, 6),
        "total_usd": round(total_usd, 6),
        "total_rmb": round(total_rmb, 4),
        "budget_rmb": float(config.video_accuracy.budget_rmb),
        "over_budget": 1.0 if total_rmb > config.video_accuracy.budget_rmb else 0.0,
    }


def _extract_steps_from_text(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    steps: list[str] = []
    step_headings = (
        "一、",
        "二、",
        "三、",
        "四、",
        "五、",
        "六、",
        "七、",
        "八、",
        "九、",
        "十、",
        "一）",
        "二）",
        "三）",
        "四）",
        "五）",
        "六）",
        "七）",
        "八）",
        "九）",
        "十）",
    )
    def is_real_command(value: str) -> bool:
        candidate = value.strip()
        if not candidate:
            return False
        if len(candidate) < 6:
            return False
        if len(candidate) > 180:
            return False
        if candidate.count(" ") > 24:
            return False
        if len(candidate) > 90 and any(token in candidate for token in ["方法", "用法", "说明", "安装后", "直接用", "报告结构"]):
            return False
        if candidate.lower() in {"openclaw", "clawdbot", "moltbot", "claude", "clawdbot", "win", "enter", "\"yes\"", "yes"}:
            return False
        if any(token in candidate for token in [" ", "-", "/", "|", "\\", "http", "openclaw", "set-executionpolicy", "iwr"]):
            return True
        return False

    for line in lines:
        if len(line) > 260:
            continue
        if line.startswith(step_headings):
            steps.append(line)
            continue
        if line.startswith("步骤") and len(line) <= 120:
            steps.append(line)
            continue
        if line.startswith("命令："):
            cmd = line[len("命令：") :].strip()
            if is_real_command(cmd):
                steps.append(f"命令：{cmd}")
            continue
        if "命令：" in line:
            for part in line.split("命令：")[1:]:
                cmd = part.strip()
                if is_real_command(cmd):
                    steps.append(f"命令：{cmd}")
        for match in re.finditer(r"([一二三四五六七八九十]+、[^\\s，。；;：:]{1,24})", line):
            steps.append(match.group(1))
        for match in re.finditer(r"([一二三四五六七八九十]+）[^\\s，。；;：:]{1,24})", line):
            steps.append(match.group(1))
    return steps[:30]


def _normalize_requirement_token(value: str) -> str:
    text = str(value).strip()
    if text.startswith(("http://", "https://")):
        text = _normalize_source_url_for_cache(text)
    text = text.lower()
    return re.sub(r"\s+", " ", text)


def _summary_signal_coverage(summary: SummaryResult, evidence) -> tuple[float, list[str]]:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
    profile = metadata.get("content_profile", {}) if isinstance(metadata.get("content_profile"), dict) else {}
    if not profile:
        profile = infer_content_profile(evidence.source_kind, evidence.source_url, evidence.text, metadata)
    required = iter_required_signal_entries(profile, signals)
    checklist_requirements = 0
    if not required and not profile.get("require_action_checklist") and not profile.get("require_project_section"):
        return 1.0, []
    corpus = "\n".join(
        [
            summary.title or "",
            summary.conclusion or "",
            *[str(item) for item in summary.bullets],
            *[str(item) for item in summary.evidence_quotes],
            *[str(item) for item in summary.follow_up_actions],
        ]
    ).lower()
    missing: list[str] = []
    hits = 0
    for key, token in required:
        normalized = _normalize_requirement_token(token)
        if normalized and normalized in corpus:
            hits += 1
        else:
            missing.append(f"{key}:{token}")
    total = len(required)
    if profile.get("require_action_checklist"):
        checklist_requirements += 1
        total += 1
        if len(summary.follow_up_actions) >= 2:
            hits += 1
        else:
            missing.append("section:执行清单")
        commands = signals.get("commands", []) if isinstance(signals.get("commands"), list) else []
        validations = signals.get("validation_actions", []) if isinstance(signals.get("validation_actions"), list) else []
        action_corpus = "\n".join(summary.follow_up_actions).lower()
        if commands:
            checklist_requirements += 1
            total += 1
            if any(_normalize_requirement_token(item) in action_corpus for item in commands[:1]):
                hits += 1
            else:
                missing.append("actions:关键命令")
        if validations:
            checklist_requirements += 1
            total += 1
            if any(_normalize_requirement_token(item) in action_corpus for item in validations[:1]):
                hits += 1
            else:
                missing.append("actions:验证动作")
    if profile.get("require_project_section"):
        total += 1
        if any(
            line.startswith(("项目名称:", "GitHub地址:", "关键链接:", "视频链接:", "技能名:", "技能ID:"))
            for line in summary.bullets
        ):
            hits += 1
        else:
            missing.append("section:项目与链接")
    return hits / max(1, total), missing


def _bullet_parts(value: str) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", str(value).strip()).strip("。；;")
    if not text:
        return "", ""
    for sep in (":", "："):
        if sep not in text:
            continue
        label, rest = text.split(sep, 1)
        if len(label.strip()) <= 12:
            return label.strip(), rest.strip()
    return "", text


def _is_resource_bullet(value: str) -> bool:
    label, body = _bullet_parts(value)
    lowered = body.lower()
    return label in _RESOURCE_BULLET_LABELS or lowered.startswith(("http://", "https://"))


def _is_boundary_text(value: str) -> bool:
    label, body = _bullet_parts(value)
    text = f"{label} {body}".lower()
    if label in _BOUNDARY_BULLET_LABELS:
        return True
    return any(token in text for token in ["边界", "限制", "不支持", "仅支持", "注意事项", "风险", "报错", "错误", "失败", "证据缺口"])


def _is_pseudo_summary_text(value: str) -> bool:
    text = re.sub(r"\s+", " ", str(value).strip()).strip("。；;")
    lowered = text.lower()
    if not text or len(text) < 6:
        return True
    if any(token in lowered for token in _PSEUDO_SUMMARY_TOKENS):
        return True
    if text.startswith(("适合", "值得", "建议", "当前可先", "先留档", "后续再看")) and len(text) <= 28:
        return True
    return False


def _summary_hard_fact_bullets(summary: SummaryResult) -> list[str]:
    hard_facts: list[str] = []
    for raw in summary.bullets:
        line = re.sub(r"\s+", " ", str(raw).strip()).strip("。；;")
        if not line or _is_resource_bullet(line):
            continue
        label, body = _bullet_parts(line)
        if _is_boundary_text(line):
            if not _is_pseudo_summary_text(body or line):
                hard_facts.append(line)
            continue
        if label in _HARD_FACT_BULLET_LABELS:
            hard_facts.append(line)
            continue
        if _is_pseudo_summary_text(body or line):
            continue
        fact_text = body or line
        if len(fact_text) >= 6 and any(
            token in fact_text for token in ["包含", "支持", "完成", "启动", "配对", "运行", "部署", "验证", "步骤", "命令", "网关", "语音轨道", "关键步骤", "摘要", "项目", "仓库"]
        ):
            hard_facts.append(line)
            continue
        if len(fact_text) >= 12 and (
            re.search(r"[A-Za-z0-9_/.-]", fact_text)
            or re.search(r"[，。；：:,]", fact_text)
            or len(fact_text) >= 18
        ):
            hard_facts.append(line)
    deduped: list[str] = []
    for item in hard_facts:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _summary_quality_details(summary: SummaryResult, evidence) -> dict[str, object]:
    signal_coverage, missing = _summary_signal_coverage(summary, evidence)
    conclusion = re.sub(r"\s+", " ", (summary.conclusion or "").strip())
    hard_fact_bullets = _summary_hard_fact_bullets(summary)
    hard_fact_count = len(hard_fact_bullets)
    resource_count = sum(1 for item in summary.bullets if _is_resource_bullet(str(item)))
    resource_only_ratio = resource_count / max(1, len(summary.bullets))
    boundary_present = any(_is_boundary_text(item) for item in summary.bullets) or any(
        _is_boundary_text(item) for item in summary.uncertainties
    ) or _is_boundary_text(conclusion)
    signal_coverage_v2 = min(
        1.0,
        0.5 * signal_coverage + 0.35 * min(1.0, hard_fact_count / 4.0) + 0.15 * (1.0 if boundary_present else 0.0),
    )
    score = signal_coverage_v2
    if hard_fact_count >= 3:
        score += 0.12
    elif hard_fact_count == 2:
        score -= 0.05
    else:
        score -= 0.22
    if len(summary.bullets) < 3:
        score -= 0.08
    if len(conclusion) < 12:
        score -= 0.08
    if _is_pseudo_summary_text(conclusion):
        score -= 0.18
    if resource_only_ratio > 0.5:
        score -= 0.15
    if evidence.source_kind != "video_url" and not boundary_present:
        score -= 0.08
    if summary.follow_up_actions and len(summary.follow_up_actions) >= 2:
        score += 0.03
    elif missing and any(item.startswith(("section:执行清单", "actions:")) for item in missing):
        score -= 0.12
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    video_gate = metadata.get("video_gate_reasons") if isinstance(metadata, dict) else None
    evidence_sources = metadata.get("evidence_sources", []) if isinstance(metadata, dict) else []
    reasons: list[str] = []
    if missing:
        reasons.append("missing_signals:" + ",".join(missing[:4]))
    if len(summary.bullets) < 3:
        reasons.append("too_few_bullets")
    if len(conclusion) < 12:
        reasons.append("conclusion_too_short")
    if _is_pseudo_summary_text(conclusion):
        reasons.append("conclusion_too_generic")
    if hard_fact_count < 3:
        reasons.append(f"hard_facts_lt3:{hard_fact_count}")
    if evidence.source_kind != "video_url" and not boundary_present:
        reasons.append("boundary_missing")
    if resource_only_ratio > 0.5:
        reasons.append(f"resource_ratio_high:{round(resource_only_ratio, 2)}")
    if evidence.source_kind == "video_url":
        informative_video_bullets = [
            item
            for item in summary.bullets
            if re.sub(r"\s+", " ", str(item).strip())
            and not _is_pseudo_summary_text(str(item))
            and not _is_resource_bullet(str(item))
        ]
        if len(conclusion) >= 18 and not _is_pseudo_summary_text(conclusion):
            score += 0.08
        else:
            score -= 0.06
            reasons.append("video_topic_clarity_low")
        if len(informative_video_bullets) >= 3:
            score += 0.08
        elif len(informative_video_bullets) <= 1:
            score -= 0.08
            reasons.append("video_mainline_bullets_low")
        if isinstance(video_gate, list) and video_gate:
            score -= 0.35
            reasons.append("video_incomplete")
        if isinstance(evidence_sources, list):
            normalized_sources = {str(item) for item in evidence_sources}
            weak_only = {"user_raw_text", "video_page_snapshot", "video_html_fallback"}
            if normalized_sources and normalized_sources.issubset(weak_only):
                score -= 0.15
                reasons.append("video_page_snapshot_only")
        if len((evidence.text or "").strip()) < 180:
            score -= 0.1
            reasons.append("video_evidence_short")
        quality_gate = metadata.get("quality_gate", {}) if isinstance(metadata.get("quality_gate"), dict) else {}
        gate_outcome = str(quality_gate.get("outcome", "")).strip().lower()
        gate_mode = str(quality_gate.get("mode", "")).strip().lower()
        probe_quality = str(quality_gate.get("probe_quality", "")).strip().lower()
        expected_outline_count = int(quality_gate.get("expected_outline_count", 0) or 0)
        observed_outline_count = int(quality_gate.get("observed_outline_count", 0) or 0)
        retained_outline_count = int(quality_gate.get("retained_outline_count", observed_outline_count) or observed_outline_count)
        if summary.outcome == "refused" or gate_outcome == "refused":
            score = min(score, 0.45)
            if "video_refused" not in reasons:
                reasons.append("video_refused")
        elif summary.outcome == "partial" or gate_outcome == "partial":
            if gate_mode == "probe":
                max_score = 0.79 if probe_quality in {"usable", "strong"} else 0.62
                score = min(score, max_score)
                if "video_probe_partial" not in reasons:
                    reasons.append("video_probe_partial")
            else:
                score = min(score, 0.69)
                if "video_partial_only" not in reasons:
                    reasons.append("video_partial_only")
        if expected_outline_count > 0 and retained_outline_count < expected_outline_count:
            reasons.append(f"outline_partial:{retained_outline_count}/{expected_outline_count}")
    score = max(0.0, min(1.0, score))
    return {
        "quality_score": score,
        "signal_coverage": signal_coverage,
        "signal_coverage_v2": signal_coverage_v2,
        "hard_fact_count": hard_fact_count,
        "boundary_present": boundary_present,
        "resource_only_ratio": resource_only_ratio,
        "reasons": reasons,
        "hard_fact_bullets": hard_fact_bullets[:5],
    }


def _summary_quality_score(summary: SummaryResult, evidence) -> tuple[float, list[str], float]:
    details = _summary_quality_details(summary, evidence)
    return (
        float(details["quality_score"]),
        list(details["reasons"]),
        float(details["signal_coverage_v2"]),
    )


def _build_low_information_partial(summary: SummaryResult, evidence, quality: dict[str, object]) -> SummaryResult:
    hard_facts = [str(item) for item in quality.get("hard_fact_bullets", []) if str(item).strip()]
    if not hard_facts:
        hard_facts = [item for item in _signal_priority_bullets(evidence, limit=4) if not _is_resource_bullet(item)]
    bullets = hard_facts[:3]
    gap_line = "证据缺口: 当前只抽到少量可核对细节，不能把这版结果当完整总结使用。"
    if not any(_is_boundary_text(item) for item in bullets):
        bullets.append(gap_line)
    conclusion_core = ""
    if bullets:
        _, body = _bullet_parts(bullets[0])
        conclusion_core = body or bullets[0]
    title = _clean_fallback_title(summary.title or evidence.title)
    conclusion = (
        f"当前只能确认《{title}》的少量硬信息，已能核实{conclusion_core}，但还不足以可靠还原更多细节。"
        if conclusion_core
        else f"当前只能确认《{title}》的主题，硬信息不足，不能把这版结果当完整总结。"
    )
    follow_up_actions = list(summary.follow_up_actions[:1])
    refill_action = "如果后续确实要用这条内容，再补原文、完整页面或更稳定证据后重跑。"
    if refill_action not in follow_up_actions:
        follow_up_actions.append(refill_action)
    uncertainties = list(summary.uncertainties)
    uncertainty = "当前硬信息少于 3 条，已自动降级为 partial。"
    if uncertainty not in uncertainties:
        uncertainties.append(uncertainty)
    return SummaryResult(
        title=summary.title,
        primary_topic=summary.primary_topic,
        secondary_topics=list(summary.secondary_topics),
        entities=list(summary.entities),
        conclusion=conclusion,
        bullets=bullets[:4],
        evidence_quotes=list(summary.evidence_quotes[:3]),
        coverage="partial",
        confidence="low" if int(quality.get("hard_fact_count", 0) or 0) < 2 else "medium",
        note_tags=list(dict.fromkeys([*summary.note_tags, "low_information_partial"])),
        follow_up_actions=follow_up_actions[:2],
        timeliness=summary.timeliness,
        effectiveness="low" if summary.effectiveness == "high" else summary.effectiveness,
        recommendation_level="optional" if summary.recommendation_level != "skip" else "skip",
        reader_judgment="当前只适合先留档，真正要用时还需要补原文或更多证据。",
        outcome="partial",
        evidence_basis=list(summary.evidence_basis),
        timeline_sections=list(summary.timeline_sections),
        uncertainties=uncertainties[:6],
        refusal_reason=summary.refusal_reason,
        finance_matrix=list(summary.finance_matrix),
        finance_snapshot=dict(summary.finance_snapshot),
    )


def _theme_only_has_specific_evidence(evidence) -> bool:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
    if isinstance(signals, dict) and any(
        signals.get(key) for key in ["projects", "skills", "skill_ids", "commands", "purposes", "use_cases", "boundaries", "common_errors"]
    ):
        return True
    description = re.sub(r"\s+", " ", str(metadata.get("bilibili_description") or "").strip())
    if description and len(description) >= 12:
        return True
    title = re.sub(r"\s+", " ", str(evidence.title or "").strip())
    for line in [re.sub(r"\s+", " ", item.strip()) for item in (evidence.text or "").splitlines() if item.strip()]:
        if line == title or line == (evidence.source_url or "").strip():
            continue
        if line.startswith(("http://", "https://")):
            continue
        if len(line) >= 14:
            return True
    return False


def _video_assessment(evidence, config: AppConfig, gate: dict[str, object] | None = None) -> dict[str, object] | None:
    if evidence.source_kind != "video_url":
        return None
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    sources = metadata.get("evidence_sources", []) if isinstance(metadata.get("evidence_sources"), list) else []
    gate_payload = gate if isinstance(gate, dict) else (metadata.get("quality_gate", {}) if isinstance(metadata.get("quality_gate"), dict) else {})
    outcome = str(gate_payload.get("outcome", "")).strip().lower()
    mode = str(gate_payload.get("mode", "")).strip().lower()
    probe_quality = str(gate_payload.get("probe_quality", "")).strip().lower()
    reasons = [str(item) for item in gate_payload.get("reasons", [])] if isinstance(gate_payload.get("reasons"), list) else []
    speech_chars = int(gate_payload.get("speech_chars", 0) or 0)
    if outcome == "summarized":
        level = "strong"
        score = 9
        next_step = "当前证据与总结状态一致，可直接人工复核。"
    elif outcome == "partial":
        if mode == "probe":
            level = "medium" if probe_quality in {"usable", "strong"} else "weak"
            score = 6 if probe_quality == "strong" else 5 if probe_quality == "usable" else 3
            next_step = "当前已拿到部分可靠内容，可先判断主题和部分要点；如需完整结论请拉长抽取。"
        else:
            level = "medium"
            score = 5
            next_step = "当前只适合做部分理解，优先补充证据或确认结构覆盖范围。"
    else:
        level = "weak"
        score = 2
        next_step = "优先补抓字幕或稳定语音轨，再判断视频结论是否可信。"
    return {
        "level": level,
        "score": score,
        "reasons": reasons,
        "text_chars": len((evidence.text or "").strip()),
        "speech_chars": speech_chars,
        "evidence_sources": sources,
        "next_step": next_step,
    }


def _infer_entry_context(ingest: IngestRequest) -> dict[str, object]:
    chat_id = str(ingest.chat_id or "").strip()
    chat_target = "group_chat" if chat_id.startswith("-") else "direct_chat"
    return {
        "chat_target": chat_target,
        "reply_to_message_id": ingest.reply_to_message_id,
        "platform_hint": ingest.platform_hint or "",
        "source_kind": ingest.source_kind,
        "has_source_url": bool(ingest.source_url),
        "has_raw_text": bool((ingest.raw_text or "").strip()),
        "image_count": len(ingest.image_refs),
    }


def _clean_fallback_title(title: str | None) -> str:
    value = re.sub(r"\s+", " ", (title or "").strip())
    if not value:
        return "未命名内容"
    value = re.sub(r"^(GitHub|github)\s*-\s*", "", value).strip()
    value = re.sub(r"[_\-\s]*哔哩哔哩[_\-\s]*bilibili$", "", value, flags=re.IGNORECASE).strip()
    value = re.sub(r"\s*[-|]\s*哔哩哔哩.*$", "", value, flags=re.IGNORECASE).strip()
    duplicate_match = re.match(r"^(.+?)\s*-\s*\1$", value)
    if duplicate_match:
        value = duplicate_match.group(1).strip()
    if len(value) > 80:
        value = value[:80].rstrip()
    return value or "未命名内容"


def _build_fallback_summary(evidence) -> SummaryResult:
    if evidence.source_kind == "video_url":
        blocks = get_qualified_video_story_blocks(evidence)
        if blocks:
            title = _clean_fallback_title(evidence.title)
            block_map = {str(item.get("label", "")).strip(): item for item in blocks if isinstance(item, dict)}
            ordered_bullets: list[str] = []
            for label in ["core_topic", "workflow", "implementation", "risk", "viewer_feedback", "other"]:
                block = block_map.get(label)
                if not block:
                    continue
                summary = re.sub(r"\s+", " ", str(block.get("summary", "")).strip())
                if summary and summary not in ordered_bullets:
                    ordered_bullets.append(summary)
            ordered_bullets = ordered_bullets[:5]

            core_summary = re.sub(r"\s+", " ", str(block_map.get("core_topic", {}).get("summary", "")).strip())
            workflow_summary = re.sub(r"\s+", " ", str(block_map.get("workflow", {}).get("summary", "")).strip())
            risk_summary = re.sub(r"\s+", " ", str(block_map.get("risk", {}).get("summary", "")).strip())
            if core_summary and workflow_summary:
                conclusion = f"{core_summary.rstrip('。')}；同时{workflow_summary.rstrip('。')}。"
            elif core_summary:
                conclusion = core_summary if core_summary.endswith("。") else core_summary + "。"
            elif ordered_bullets:
                conclusion = ordered_bullets[0].rstrip("。") + "。"
            else:
                conclusion = f"视频主要围绕《{title}》展开。"
            if risk_summary and "盲目跟单" in risk_summary and "技术展示" not in conclusion:
                conclusion = conclusion.rstrip("。") + "，整体更偏技术展示而非直接投资建议。"

            evidence_quotes: list[str] = []
            for block in blocks:
                for item in block.get("evidence", []) if isinstance(block.get("evidence", []), list) else []:
                    text = re.sub(r"\s+", " ", str(item).strip())
                    if not text or text in evidence_quotes:
                        continue
                    evidence_quotes.append(text)
                    if len(evidence_quotes) >= 4:
                        break
                if len(evidence_quotes) >= 4:
                    break
            if not evidence_quotes:
                evidence_quotes = [item for item in ordered_bullets[:2] if item]

            viewer_feedback = get_viewer_feedback(evidence)
            follow_up_actions: list[str] = []
            if any(token in (evidence.text or "") for token in ["股票", "自选股", "买入", "持有", "量化"]):
                follow_up_actions = [
                    "回到原视频确认部署方式、触发入口和推送链路。",
                    "如果准备实操，先自行复核信源、回测结果和风险边界。",
                ]
            elif viewer_feedback:
                follow_up_actions = [
                    "回看原视频确认观众争议点对应的上下文。",
                    "把评论区里的扩展问题单独整理成后续验证项。",
                ]

            summary = SummaryResult(
                title=title,
                primary_topic="视频",
                secondary_topics=["股票", "自动化"] if any(token in (evidence.text or "") for token in ["股票", "自选股"]) else [],
                entities=["OpenClaw"] if "openclaw" in ((evidence.text or "") + " " + (evidence.title or "")).lower() else [],
                conclusion=conclusion,
                bullets=ordered_bullets or ["视频核心信息已提取。"],
                evidence_quotes=evidence_quotes,
                coverage=evidence.coverage or "partial",
                confidence="high" if block_map.get("workflow") and block_map.get("implementation") else "medium",
                note_tags=["video_fallback", "story_blocks"],
                follow_up_actions=follow_up_actions,
                recommendation_level="recommended" if ordered_bullets else "optional",
                timeliness="medium",
                effectiveness="high" if block_map.get("workflow") else "medium",
                reader_judgment="当前能看出流程主线，但具体参数和细节仍建议回原视频核对。",
            )
            return _validate_and_normalize_summary(summary, evidence)

    title = _clean_fallback_title(evidence.title)
    primary_topic = "未分类"
    secondary_topics: list[str] = []
    entities: list[str] = []
    bullets: list[str] = _signal_priority_bullets(evidence, limit=5)
    signals = evidence.metadata.get("signals", {}) if isinstance(evidence.metadata, dict) else {}

    step_items = None
    steps = None
    if isinstance(evidence.metadata, dict):
        step_items = evidence.metadata.get("step_items")
        steps = evidence.metadata.get("steps")
    if step_items:
        for item in step_items[:7]:
            title_part = item.get("title") or ""
            detail = item.get("detail") or ""
            line = title_part
            if detail:
                line = f"{line}：{detail}" if line else detail
            if line and line not in bullets:
                bullets.append(line)
    elif steps:
        for step in steps[:7]:
            text = str(step).strip()
            if text and text not in bullets:
                bullets.append(text)
    else:
        for line in [line.strip() for line in evidence.text.splitlines() if line.strip()]:
            if len(line) < 8 or len(line) > 120:
                continue
            if line == (evidence.source_url or "").strip():
                continue
            if re.fullmatch(r"https?://\S+", line):
                continue
            if any(token in line for token in ["行吟信息科技", "地址：", "电话：", "备案号", "违法和不良信息举报"]):
                continue
            if re.search(r"[？?]$", line) and len(line) <= 36:
                continue
            if any(line.startswith(prefix) for prefix in ["你这个", "我是", "我想问", "请问"]):
                continue
            if line.startswith("[提取到的关键信号]"):
                continue
            if line.startswith("[") and line.endswith("]"):
                continue
            if line.startswith("链接: ") or line.startswith("技能名: ") or line.startswith("命令: "):
                continue
            if line not in bullets:
                bullets.append(line)
            if len(bullets) >= 5:
                break
    if not bullets:
        bullets = ["证据缺口: 当前没有抽到足够的正文细节，不能把这版结果当成完整总结。"]
    hard_facts = [item for item in bullets if not _is_resource_bullet(item) and not _is_pseudo_summary_text(item)]
    boundary_line = next((item for item in bullets if _is_boundary_text(item)), "")
    if not boundary_line:
        boundary_line = "证据缺口: 当前只抽到少量可核对细节，更多内容需要回原文确认。"
        bullets.append(boundary_line)
    lead_bullet = hard_facts[0] if hard_facts else bullets[0]
    _, lead_body = _bullet_parts(lead_bullet)
    _, boundary_body = _bullet_parts(boundary_line)
    lead_text = lead_body or lead_bullet
    boundary_text = boundary_body or boundary_line
    conclusion = f"当前可确认的核心信息是{lead_text}；{boundary_text}"
    if not conclusion.endswith("。"):
        conclusion += "。"
    coverage = evidence.coverage or "partial"
    confidence = "medium"
    outcome = "summarized"
    reader_judgment = ""
    if len(hard_facts) < 3 or evidence.coverage == "partial":
        coverage = "partial"
        confidence = "low" if len(hard_facts) < 2 else "medium"
        outcome = "partial"
        reader_judgment = "当前只适合先留档，真正要用时还需要补原文或更多证据。"

    evidence_quotes: list[str] = []
    for line in [line.strip() for line in evidence.text.splitlines() if line.strip()]:
        if 8 <= len(line) <= 40 and line not in evidence_quotes:
            if line == (evidence.source_url or "").strip():
                continue
            if re.fullmatch(r"https?://\S+", line):
                continue
            if any(token in line for token in ["行吟信息科技", "地址：", "电话：", "备案号"]):
                continue
            if re.search(r"[？?]$", line):
                continue
            if line.startswith("[") and line.endswith("]"):
                continue
            evidence_quotes.append(line)
        if len(evidence_quotes) >= 3:
            break
    if not evidence_quotes:
        evidence_quotes = ["无可用摘录。"]

    summary = SummaryResult(
        title=title,
        primary_topic=primary_topic,
        secondary_topics=secondary_topics,
        entities=entities,
        conclusion=conclusion,
        bullets=bullets[:5],
        evidence_quotes=evidence_quotes,
        coverage=coverage,
        confidence=confidence,
        note_tags=[],
        follow_up_actions=[],
        reader_judgment=reader_judgment,
        outcome=outcome,
        uncertainties=[boundary_text] if outcome == "partial" else [],
    )
    return _validate_and_normalize_summary(summary, evidence)


class WorkflowProcessor:
    def __init__(self, config: AppConfig, jobs: JobStore, summarizer: SummaryEngine, base_state_dir: Path) -> None:
        self.config = config
        self.jobs = jobs
        self.summarizer = summarizer
        self.video_summarizer = EvidenceDrivenVideoSummarizer(
            config.video_summary,
            client=AiHubMixGeminiSummarizer(config.video_summary),
        )
        self._upgrade_summarizer: OpenAICompatibleSummarizer | None = None
        if (
            config.summary_routing.enabled
            and config.summary_routing.upgrade_model.strip()
            and isinstance(summarizer, OpenAICompatibleSummarizer)
            and config.summary_routing.upgrade_model.strip() != config.summarizer.model
        ):
            upgrade_cfg = SummarizerConfig(
                api_base_url=config.summarizer.api_base_url,
                api_key=config.summarizer.api_key,
                model=config.summary_routing.upgrade_model.strip(),
                timeout_seconds=config.summarizer.timeout_seconds,
            )
            self._upgrade_summarizer = OpenAICompatibleSummarizer(upgrade_cfg)
        self.base_state_dir = base_state_dir
        self.extractor = EvidenceExtractor(config, base_state_dir / "artifacts")
        self.note_renderer = OpenAICompatibleNoteRenderer(config.summarizer) if isinstance(summarizer, OpenAICompatibleSummarizer) else None
        self.writer = ObsidianWriter(
            config.obsidian,
            renderer=self.note_renderer,
            materials_root=base_state_dir / "materials",
        )
        self.notifier = TelegramNotifier(config.telegram.result_bot_token)
        self.summary_cache_dir = base_state_dir / "summary_cache"
        self.summary_cache_dir.mkdir(parents=True, exist_ok=True)
        self.preview_dir = base_state_dir / "previews"
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        self._queue: "queue.Queue[IngestRequest]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def _can_upgrade_summary(self, ingest: IngestRequest) -> bool:
        if not self.config.summary_routing.enabled:
            return False
        if self._upgrade_summarizer is None:
            return False
        if ingest.dry_run and not self.config.summary_routing.apply_on_dry_run:
            return False
        return True

    def _should_upgrade_for_quality(self, summary: SummaryResult, evidence) -> tuple[bool, dict[str, object]]:
        details = _summary_quality_details(summary, evidence)
        score = float(details["quality_score"])
        reasons = list(details["reasons"])
        coverage = float(details["signal_coverage_v2"])
        threshold = float(self.config.summary_routing.low_quality_threshold)
        min_signal_coverage = float(self.config.summary_routing.min_signal_coverage)
        hard_fact_count = int(details.get("hard_fact_count", 0) or 0)
        need_upgrade = score < threshold or coverage < min_signal_coverage
        if evidence.source_kind != "video_url" and hard_fact_count < 3:
            need_upgrade = True
        if need_upgrade and score < threshold:
            reasons.append(f"quality_score={round(score,3)}<threshold={round(threshold,3)}")
        if need_upgrade and coverage < min_signal_coverage:
            reasons.append(f"signal_coverage={round(coverage,3)}<min={round(min_signal_coverage,3)}")
        if need_upgrade and evidence.source_kind != "video_url" and hard_fact_count < 3:
            reasons.append(f"hard_fact_count={hard_fact_count}<3")
        details["reasons"] = reasons
        return need_upgrade, details

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker, name="capture-workflow", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(IngestRequest(chat_id="", reply_to_message_id=None, request_id="__stop__", source_kind="noop"))
        if self._thread:
            self._thread.join(timeout=2)

    def enqueue(self, ingest: IngestRequest) -> JobRecord:
        job = JobRecord.queued(ingest)
        self.jobs.save(job)
        self._queue.put(ingest)
        return job

    def _should_use_summary_cache(self, ingest: IngestRequest, evidence) -> bool:
        if not self.config.execution.enable_summary_cache:
            return False
        if not evidence.source_url:
            return False
        if evidence.source_kind == "video_url":
            gate = evaluate_quality_gate(evidence, self.config)
            if gate.outcome == "refused":
                return False
        if ingest.dry_run and not self.config.execution.cache_for_dry_run:
            return False
        if not ingest.dry_run and not self.config.execution.cache_for_non_dry_run:
            return False
        return True

    def _summary_cache_path(self, source_url: str) -> Path:
        key = hashlib.sha1(source_url.encode("utf-8")).hexdigest()
        return self.summary_cache_dir / f"{key}.json"

    def _load_cached_summary(self, evidence) -> tuple[SummaryResult | None, str]:
        source_key = _normalize_source_url_for_cache(evidence.source_url)
        if not source_key:
            return None, ""
        path = self._summary_cache_path(source_key)
        if not path.exists() or not path.is_file():
            return None, ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, source_key

        created_at = _parse_utc_timestamp(str(payload.get("created_at", "")))
        if created_at is None:
            return None, source_key
        ttl_hours = max(1, int(self.config.execution.summary_cache_ttl_hours))
        if datetime.now(timezone.utc) - created_at > timedelta(hours=ttl_hours):
            return None, source_key

        cached_fp = str(payload.get("fingerprint", ""))
        current_fp = _evidence_fingerprint(evidence)
        if not cached_fp or cached_fp != current_fp:
            return None, source_key
        raw_summary = payload.get("summary")
        if not isinstance(raw_summary, dict):
            return None, source_key
        try:
            summary = SummaryResult(**raw_summary)
        except TypeError:
            return None, source_key
        return summary, source_key

    def _save_summary_cache(self, evidence, summary: SummaryResult) -> str:
        source_key = _normalize_source_url_for_cache(evidence.source_url)
        if not source_key:
            return ""
        payload = {
            "source_url": source_key,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "fingerprint": _evidence_fingerprint(evidence),
            "summary": summary.to_dict(),
        }
        path = self._summary_cache_path(source_key)
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return source_key
        return source_key

    def _save_note_preview_file(self, request_id: str, note_preview: dict) -> str | None:
        content = str(note_preview.get("content", "")).strip()
        if not content:
            return None
        path = self.preview_dir / f"{request_id}.md"
        try:
            path.write_text(content + "\n", encoding="utf-8")
        except OSError:
            return None
        return str(path)

    def _worker(self) -> None:
        while not self._stop.is_set():
            ingest = self._queue.get()
            job = None
            current_phase: str | None = None
            evidence = None
            try:
                if ingest.request_id == "__stop__":
                    continue
                job = self.jobs.load(ingest.request_id)
                if not job:
                    job = JobRecord.queued(ingest)
                job.ensure_tracking_fields()

                job.mark("processing", message="extracting evidence")
                current_phase = "extract"
                job.set_phase("extract", "processing")
                self.jobs.save(job)
                evidence = self.extractor.extract(ingest)
                job.set_phase("extract", "done")
                video_recovery: dict[str, object] | None = None
                if (
                    evidence.source_kind == "video_url"
                    and not ingest.dry_run
                    and self.config.video_accuracy.retry_on_incomplete
                ):
                    current_reasons = _video_gate_reasons(evidence, self.config)
                    if current_reasons:
                        retry_ingest = IngestRequest(
                            chat_id=ingest.chat_id,
                            reply_to_message_id=ingest.reply_to_message_id,
                            request_id=ingest.request_id,
                            source_kind=ingest.source_kind,
                            source_url=ingest.source_url,
                            raw_text=ingest.raw_text,
                            image_refs=list(ingest.image_refs),
                            platform_hint=ingest.platform_hint,
                            requested_output_lang=ingest.requested_output_lang,
                            dry_run=False,
                            video_probe_seconds=None,
                            force_full_video=True,
                        )
                        retry_extractor = self.extractor
                        if (
                            self.config.video_accuracy.retry_force_audio
                            and not self.config.video_accuracy.always_run_audio
                            and isinstance(self.extractor, EvidenceExtractor)
                        ):
                            retry_cfg = replace(
                                self.config,
                                video_accuracy=replace(self.config.video_accuracy, always_run_audio=True),
                            )
                            retry_extractor = EvidenceExtractor(retry_cfg, self.base_state_dir / "artifacts")
                        try:
                            recovered = retry_extractor.extract(retry_ingest)
                            recovered_reasons = _video_gate_reasons(recovered, self.config)
                            apply_recovered = _is_video_recovery_better(
                                evidence,
                                recovered,
                                current_reasons,
                                recovered_reasons,
                                self.config.video_accuracy.retry_min_char_gain,
                            )
                            video_recovery = {
                                "attempted": True,
                                "applied": apply_recovered,
                                "before_reasons": current_reasons,
                                "after_reasons": recovered_reasons,
                                "before_track_score": _video_track_score(evidence),
                                "after_track_score": _video_track_score(recovered),
                                "before_text_chars": len((evidence.text or "").strip()),
                                "after_text_chars": len((recovered.text or "").strip()),
                                "force_audio": bool(self.config.video_accuracy.retry_force_audio),
                            }
                            if apply_recovered:
                                evidence = recovered
                                if isinstance(evidence.metadata, dict):
                                    evidence.metadata["video_recovery"] = video_recovery
                                job.add_warning(
                                    "video_recovery_applied: "
                                    + f"reasons {len(current_reasons)} -> {len(recovered_reasons)}"
                                )
                            else:
                                job.add_warning(
                                    "video_recovery_not_improved: "
                                    + f"reasons {len(current_reasons)} -> {len(recovered_reasons)}"
                                )
                        except Exception as exc:
                            video_recovery = {"attempted": True, "applied": False, "error": str(exc)}
                            job.add_warning(f"video_recovery_failed: {exc}")

                job.mark("processing", message="summarizing")
                current_phase = "summarize"
                job.set_phase("summarize", "processing")
                self.jobs.save(job)
                quality_gate = evaluate_quality_gate(evidence, self.config)
                if evidence.source_kind != "video_url" and not _has_sufficient_evidence_text(
                    evidence.source_kind,
                    evidence.text,
                    evidence.source_url,
                    evidence.metadata,
                    self.config.evidence_gate,
                ):
                    raise RuntimeError("insufficient evidence extracted from source; refusing to summarize")
                if isinstance(evidence.metadata, dict):
                    evidence.metadata["quality_gate"] = quality_gate.to_dict()
                if quality_gate.reasons:
                    job.add_warning("quality_gate: " + "; ".join(quality_gate.reasons[:4]))
                    if evidence.source_kind == "video_url":
                        gate_reasons = list(quality_gate.reasons)
                        should_flag_incomplete = (
                            quality_gate.mode == "full"
                            or quality_gate.outcome == "refused"
                            or quality_gate.theme_only
                            or quality_gate.probe_quality == "weak"
                        )
                        evidence.metadata["video_gate_reasons"] = gate_reasons if should_flag_incomplete else []
                        if quality_gate.mode == "probe" and not should_flag_incomplete:
                            job.add_warning("video_probe_partial: " + "; ".join(quality_gate.reasons[:4]))
                        else:
                            job.add_warning("video_evidence_incomplete: " + "; ".join(quality_gate.reasons[:4]))
                video_cost_estimate = None
                if evidence.source_kind == "video_url":
                    video_cost_estimate = _estimate_video_cost_rmb(evidence, self.config)
                    evidence.metadata["video_cost_estimate"] = video_cost_estimate
                    if video_cost_estimate["over_budget"] > 0:
                        job.add_warning(
                            "video_cost_over_budget: "
                            + f"estimated {video_cost_estimate['total_rmb']} RMB > target {video_cost_estimate['budget_rmb']} RMB"
                        )
                if evidence.text and "steps" not in evidence.metadata:
                    looks_like_tutorial = bool(
                        re.search(r"(?:^|\n)[一二三四五六七八九十][、）]", evidence.text)
                        or "步骤" in evidence.text[:4000]
                    )
                    tencent_article = bool(
                        evidence.source_url and "cloud.tencent.com/developer/article" in evidence.source_url
                    )
                    if looks_like_tutorial or tencent_article:
                        steps = _extract_steps_from_text(evidence.text)
                        if steps:
                            evidence.metadata["steps"] = steps
                if isinstance(evidence.metadata, dict):
                    refreshed_profile = infer_content_profile(
                        evidence.source_kind,
                        evidence.source_url,
                        evidence.text,
                        evidence.metadata,
                    )
                    evidence.metadata["content_profile"] = refreshed_profile
                    signals = evidence.metadata.get("signals", {}) if isinstance(evidence.metadata.get("signals"), dict) else {}
                    evidence.metadata["signal_requirements"] = build_signal_requirements(refreshed_profile, signals)
                summary_mode = "normal"
                summary_error = ""
                summary_attempts = 0
                summary_started_at = time.perf_counter()
                summary = None
                cache_key = ""
                cache_hit = False
                summary_model_used = ""
                summary_model_chain: list[str] = []
                summary_quality: dict[str, object] = {}
                if self._should_use_summary_cache(ingest, evidence):
                    cached_summary, cache_key = self._load_cached_summary(evidence)
                    if cached_summary is not None:
                        summary = cached_summary
                        summary_mode = "cache"
                        cache_hit = True
                        summary_model_used = "cache"
                        summary_model_chain = ["cache"]
                        job.add_warning("summary_cache_hit")

                if summary is None:
                    if _is_web_blocked_notice_evidence(evidence):
                        summary = _build_web_blocked_notice_summary(evidence)
                        summary_mode = "web_blocked_notice"
                        summary_model_used = "web_blocked_notice"
                        summary_model_chain = ["web_blocked_notice"]
                    elif quality_gate.outcome == "refused":
                        summary = build_refusal_summary(evidence, quality_gate)
                        summary_mode = "quality_gate_refusal"
                        summary_model_used = "quality_gate"
                        summary_model_chain = ["quality_gate"]
                    elif evidence.source_kind == "video_url":
                        summary_attempts += 1
                        if quality_gate.theme_only:
                            if _theme_only_has_specific_evidence(evidence):
                                summary = build_theme_only_summary(evidence, quality_gate)
                                summary_mode = "video_theme_only"
                                summary_model_used = "quality_gate_theme_only"
                                summary_model_chain = ["quality_gate_theme_only"]
                            else:
                                summary = build_refusal_summary(evidence, quality_gate)
                                summary_mode = "quality_gate_refusal"
                                summary_model_used = "quality_gate"
                                summary_model_chain = ["quality_gate"]
                                job.add_warning("video_theme_only_refused_due_to_missing_specific_evidence")
                        else:
                            try:
                                summary = self.video_summarizer.summarize(evidence)
                                summary_mode = "video_evidence_driven"
                                summary_model_used = self.config.video_summary.model
                                summary_model_chain = [self.config.video_summary.model, self.config.video_summary.fallback_model]
                            except Exception as exc:
                                summary_error = str(exc)
                                summary = build_model_unavailable_summary(evidence, quality_gate, summary_error)
                                summary_mode = "video_model_unavailable"
                                summary_model_used = "video_model_unavailable"
                                summary_model_chain = [self.config.video_summary.model, self.config.video_summary.fallback_model]
                                job.add_warning(f"video_summary_model_unavailable: {summary_error}")
                        if quality_gate.outcome == "partial" and summary.outcome != "refused":
                            summary.outcome = "partial"
                            partial_note = (
                                "当前是快速抽取结果，已拿到部分可靠内容，但不是整条视频完整理解。"
                                if quality_gate.mode == "probe"
                                else "当前视频证据仍然只有部分覆盖。"
                            )
                            if partial_note not in summary.uncertainties:
                                summary.uncertainties = [*summary.uncertainties, partial_note]
                            summary.coverage = "partial"
                    else:
                        primary_model = self.config.summarizer.model
                        for attempt in range(2):
                            summary_attempts += 1
                            try:
                                summary = self.summarizer.summarize(evidence)
                                summary_error = ""
                                summary_model_used = primary_model
                                if primary_model not in summary_model_chain:
                                    summary_model_chain.append(primary_model)
                                break
                            except Exception as exc:
                                summary_error = str(exc)
                                if attempt == 1:
                                    raise
                        if (
                            summary is not None
                            and summary_mode in {"normal"}
                            and self._can_upgrade_summary(ingest)
                            and self.config.summary_routing.trigger_on_low_quality
                            and summary_model_used != self.config.summary_routing.upgrade_model
                        ):
                            need_upgrade, quality_details = self._should_upgrade_for_quality(
                                summary, evidence
                            )
                            quality_score = float(quality_details["quality_score"])
                            summary_quality = {
                                "quality_score": round(quality_score, 4),
                                "signal_coverage": round(float(quality_details["signal_coverage"]), 4),
                                "signal_coverage_v2": round(float(quality_details["signal_coverage_v2"]), 4),
                                "hard_fact_count": int(quality_details["hard_fact_count"]),
                                "boundary_present": bool(quality_details["boundary_present"]),
                                "resource_only_ratio": round(float(quality_details["resource_only_ratio"]), 4),
                                "reasons": list(quality_details["reasons"]),
                            }
                            if need_upgrade:
                                upgrade_model = self.config.summary_routing.upgrade_model
                                try:
                                    summary_attempts += 1
                                    upgraded = self._upgrade_summarizer.summarize(evidence)  # type: ignore[union-attr]
                                    upgraded_details = _summary_quality_details(upgraded, evidence)
                                    upgraded_score = float(upgraded_details["quality_score"])
                                    if upgraded_score >= quality_score:
                                        summary = upgraded
                                        summary_mode = "upgraded_model"
                                        summary_model_used = upgrade_model
                                        if upgrade_model not in summary_model_chain:
                                            summary_model_chain.append(upgrade_model)
                                        summary_quality = {
                                            "quality_score": round(upgraded_score, 4),
                                            "signal_coverage": round(float(upgraded_details["signal_coverage"]), 4),
                                            "signal_coverage_v2": round(float(upgraded_details["signal_coverage_v2"]), 4),
                                            "hard_fact_count": int(upgraded_details["hard_fact_count"]),
                                            "boundary_present": bool(upgraded_details["boundary_present"]),
                                            "resource_only_ratio": round(float(upgraded_details["resource_only_ratio"]), 4),
                                            "reasons": list(upgraded_details["reasons"]),
                                            "primary_quality_score": round(quality_score, 4),
                                            "upgraded": True,
                                        }
                                        job.add_warning(
                                            f"summary_model_upgrade_low_quality: primary={primary_model} -> upgrade={upgrade_model}"
                                        )
                                    else:
                                        job.add_warning(
                                            f"summary_model_upgrade_skipped: upgrade_score={round(upgraded_score,3)} < primary_score={round(quality_score,3)}"
                                        )
                                except Exception as upgrade_exc:
                                    job.add_warning(f"summary_model_upgrade_failed: {upgrade_exc}")
                        if summary is not None and evidence.source_kind != "video_url":
                            low_info_details = _summary_quality_details(summary, evidence)
                            if int(low_info_details.get("hard_fact_count", 0) or 0) < 3:
                                summary = _build_low_information_partial(summary, evidence, low_info_details)
                                job.add_warning("summary_low_information_degraded")
                        if (
                            summary is not None
                            and summary_mode in {"normal", "upgraded_model"}
                            and self._should_use_summary_cache(ingest, evidence)
                        ):
                            cache_key = self._save_summary_cache(evidence, summary)
                assert summary is not None
                if isinstance(evidence.metadata, dict):
                    gate_payload = evidence.metadata.get("quality_gate", {})
                    if isinstance(gate_payload, dict):
                        gate_payload["retained_outline_count"] = compute_retained_outline_count(
                            evidence,
                            summary,
                            expected_outline_count=int(gate_payload.get("expected_outline_count", 0) or 0),
                        )
                        evidence.metadata["quality_gate"] = gate_payload
                        quality_gate.retained_outline_count = int(gate_payload["retained_outline_count"] or 0)
                summary_elapsed_seconds = round(max(0.0, time.perf_counter() - summary_started_at), 3)
                quality_details = _summary_quality_details(summary, evidence)
                quality_score = float(quality_details["quality_score"])
                quality_reasons = list(quality_details["reasons"])
                signal_coverage = float(quality_details["signal_coverage_v2"])
                final_quality = {
                    "quality_score": round(quality_score, 4),
                    "signal_coverage": round(float(quality_details["signal_coverage"]), 4),
                    "signal_coverage_v2": round(signal_coverage, 4),
                    "hard_fact_count": int(quality_details["hard_fact_count"]),
                    "boundary_present": bool(quality_details["boundary_present"]),
                    "resource_only_ratio": round(float(quality_details["resource_only_ratio"]), 4),
                    "reasons": quality_reasons,
                }
                if summary_quality:
                    for key, value in summary_quality.items():
                        if key not in {"quality_score", "signal_coverage", "reasons"}:
                            final_quality[key] = value
                summary_quality = final_quality
                if quality_reasons and summary_mode in {"normal", "cache", "video_evidence_driven"}:
                    job.add_warning("summary_quality_flags: " + "; ".join(quality_reasons[:3]))
                job.set_phase("summarize", "done")

                note_meta = None
                note_preview = None
                open_url = None
                notification_error = ""
                refusal_only_video = summary.outcome == "refused" and evidence.source_kind == "video_url"

                if ingest.dry_run:
                    job.set_phase("write_note", "skipped")
                    if not refusal_only_video:
                        note_preview = self.writer.preview(
                            summary,
                            evidence,
                            use_model_render=True,
                        )
                        if "content" in note_preview:
                            preview_file = self._save_note_preview_file(ingest.request_id, note_preview)
                            if preview_file:
                                note_preview["preview_file"] = preview_file
                    job.set_phase("notify", "skipped")
                else:
                    current_phase = "notify"
                    if refusal_only_video:
                        job.set_phase("write_note", "skipped")
                        job.mark("processing", message="sending refusal notification")
                        job.set_phase("notify", "processing")
                        self.jobs.save(job)
                        job.notification = {"attempted": True, "ok": None, "error": None}
                        try:
                            refusal_text = (
                                self.notifier.build_refusal_message(summary, evidence)
                                if hasattr(self.notifier, "build_refusal_message")
                                else summary.refusal_reason or summary.conclusion
                            )
                            reply_to_message_id = None
                            if ingest.reply_to_message_id:
                                try:
                                    reply_to_message_id = int(ingest.reply_to_message_id)
                                except (TypeError, ValueError):
                                    reply_to_message_id = None
                            if hasattr(self.notifier, "send_text"):
                                self.notifier.send_text(
                                    chat_id=ingest.chat_id,
                                    text=refusal_text,
                                    reply_to_message_id=reply_to_message_id,
                                )
                            elif hasattr(self.notifier, "send_result"):
                                self.notifier.send_result(ingest, summary, "", "", "")
                            job.notification = {"attempted": True, "ok": True, "error": None}
                            job.set_phase("notify", "done")
                        except Exception as exc:
                            notification_error = str(exc)
                            job.notification = {
                                "attempted": True,
                                "ok": False,
                                "error": notification_error,
                            }
                            job.set_phase("notify", "failed")
                            job.add_warning(f"notification_error: {notification_error}")
                    else:
                        current_phase = "write_note"
                        job.mark("processing", message="writing note")
                        job.set_phase("write_note", "processing")
                        self.jobs.save(job)
                        note_meta = self.writer.write(summary, evidence, use_model_render=True)
                        if note_meta.get("note_render_error"):
                            job.set_phase("write_note", "failed")
                            partial_result: Dict[str, object] = {
                                "summary": summary.to_dict(),
                                "evidence": evidence.to_dict(),
                                "dry_run": ingest.dry_run,
                                "summary_mode": summary_mode,
                                "summary_attempts": summary_attempts,
                                "summary_model": summary_model_used,
                                "summary_model_chain": summary_model_chain,
                                "entry_context": _infer_entry_context(ingest),
                                "content_profile": evidence.metadata.get("content_profile", {}) if isinstance(evidence.metadata, dict) else {},
                                "signal_requirements": evidence.metadata.get("signal_requirements", {}) if isinstance(evidence.metadata, dict) else {},
                                "evidence_sources": evidence.metadata.get("evidence_sources", []) if isinstance(evidence.metadata, dict) else [],
                                "note_render_error": note_meta.get("note_render_error"),
                                "materials_file": note_meta.get("materials_file"),
                            }
                            if summary_quality:
                                partial_result["summary_quality"] = summary_quality
                            job.mark("failed", message="note_render_failed", result=partial_result, error=str(note_meta.get("note_render_error")))
                            self.jobs.save(job)
                            continue
                        job.set_phase("write_note", "done")
                        open_url = f"{self.config.local_base_url}/open?path={quote(str(note_meta['note_path']), safe='')}"

                        current_phase = "notify"
                        job.mark("processing", message="sending notification")
                        job.set_phase("notify", "processing")
                        self.jobs.save(job)
                        job.notification = {"attempted": True, "ok": None, "error": None}
                        try:
                            try:
                                self.notifier.send_result(
                                    ingest,
                                    summary,
                                    str(note_meta["note_path"]),
                                    str(note_meta["structure_map"]),
                                    open_url,
                                    evidence,
                                    summary_model_used,
                                    summary_elapsed_seconds,
                                )
                            except TypeError as exc:
                                if "positional arguments" not in str(exc) and "keyword" not in str(exc):
                                    raise
                                self.notifier.send_result(
                                    ingest,
                                    summary,
                                    str(note_meta["note_path"]),
                                    str(note_meta["structure_map"]),
                                    open_url,
                                )
                            job.notification = {"attempted": True, "ok": True, "error": None}
                            job.set_phase("notify", "done")
                        except Exception as exc:
                            notification_error = str(exc)
                            job.notification = {
                                "attempted": True,
                                "ok": False,
                                "error": notification_error,
                            }
                            job.set_phase("notify", "failed")
                            job.add_warning(f"notification_error: {notification_error}")

                result: Dict[str, object] = {
                    "summary": summary.to_dict(),
                    "evidence": evidence.to_dict(),
                    "dry_run": ingest.dry_run,
                    "outcome": summary.outcome,
                    "capture_manifest": evidence.capture_manifest.to_dict(),
                    "evidence_digest": build_evidence_digest(evidence),
                    "refusal_reason": summary.refusal_reason,
                    "quality_gate": quality_gate.to_dict(),
                    "summary_mode": summary_mode,
                    "summary_attempts": summary_attempts,
                    "summary_model": summary_model_used,
                    "summary_model_chain": summary_model_chain,
                    "summary_elapsed_seconds": summary_elapsed_seconds,
                    "entry_context": _infer_entry_context(ingest),
                    "content_profile": evidence.metadata.get("content_profile", {}) if isinstance(evidence.metadata, dict) else {},
                    "signal_requirements": evidence.metadata.get("signal_requirements", {}) if isinstance(evidence.metadata, dict) else {},
                    "evidence_sources": evidence.metadata.get("evidence_sources", []) if isinstance(evidence.metadata, dict) else [],
                }
                if evidence.source_kind == "video_url" and isinstance(evidence.metadata, dict):
                    result["video_provider"] = str(evidence.metadata.get("video_provider") or "")
                    result["video_provider_attempts"] = evidence.metadata.get("video_provider_attempts", [])
                    result["coverage_basis"] = evidence.metadata.get("coverage_basis", {})
                    result["video_provider_capabilities"] = evidence.metadata.get("video_provider_capabilities", {})
                if cache_key:
                    result["summary_cache"] = {
                        "enabled": True,
                        "key": cache_key,
                        "hit": cache_hit,
                    }
                if summary_quality:
                    result["summary_quality"] = summary_quality
                if summary_error:
                    result["summary_error"] = summary_error
                if video_cost_estimate is not None:
                    result["video_cost_estimate"] = video_cost_estimate
                assessment_gate = quality_gate.to_dict()
                if summary.outcome == "partial" and str(assessment_gate.get("outcome", "")).strip().lower() == "summarized":
                    assessment_gate["outcome"] = "partial"
                    assessment_reasons = [str(item) for item in assessment_gate.get("reasons", [])] if isinstance(assessment_gate.get("reasons"), list) else []
                    if summary_mode == "video_model_unavailable":
                        assessment_reasons.append("video summary model unavailable")
                    assessment_gate["reasons"] = assessment_reasons
                elif summary.outcome == "refused":
                    assessment_gate["outcome"] = "refused"
                    assessment_reasons = [str(item) for item in assessment_gate.get("reasons", [])] if isinstance(assessment_gate.get("reasons"), list) else []
                    if summary.refusal_reason:
                        assessment_reasons.append(summary.refusal_reason)
                    assessment_gate["reasons"] = assessment_reasons
                video_assessment = _video_assessment(evidence, self.config, assessment_gate)
                if video_assessment is not None:
                    result["video_assessment"] = video_assessment
                if evidence.source_kind == "video_url":
                    clarity_score = score_video_summary(
                        summary.to_dict(),
                        outcome=summary.outcome,
                        quality_gate=assessment_gate,
                        status="done",
                    )
                    result["video_clarity_score"] = clarity_score.to_dict()
                    if clarity_score.total < 6:
                        job.add_warning(f"video_clarity_low: {clarity_score.total}/10")
                if video_recovery is not None:
                    result["video_recovery"] = video_recovery
                if note_meta is not None:
                    result["note"] = note_meta
                    result["open_url"] = open_url
                if note_preview is not None:
                    result["note_preview"] = note_preview
                if notification_error:
                    result["notification_error"] = notification_error

                if not ingest.dry_run and evidence.source_kind in {"url", "video_url"}:
                    relevant_warning_prefixes = (
                        "summary_quality_flags",
                        "notification_error",
                        "video_evidence_incomplete",
                        "video_cost_over_budget",
                        "video_recovery_failed",
                        "summary_model_upgrade_failed",
                        "summary_model_upgrade_skipped",
                        "video_download_failed",
                        "video_frame_sampling_failed",
                    )
                    relevant_warnings = [
                        warning for warning in job.warnings if str(warning).startswith(relevant_warning_prefixes)
                    ]
                    auto_reason = ""
                    if evidence.coverage == "partial":
                        auto_reason = "coverage_partial"
                    elif quality_score < 0.72:
                        auto_reason = "summary_quality_low"
                    if relevant_warnings or auto_reason:
                        from .iterative_cases import maybe_record_auto_case

                        maybe_record_auto_case(
                            self.base_state_dir / "cases" / "inbox.jsonl",
                            source_kind=evidence.source_kind,
                            source_url=evidence.source_url,
                            raw_text=ingest.raw_text,
                            platform_hint=ingest.platform_hint,
                            warnings=relevant_warnings,
                            coverage=evidence.coverage,
                            summary_quality_score=quality_score,
                            dry_run=ingest.dry_run,
                            labels=["processor", evidence.source_kind] + ([ingest.platform_hint] if ingest.platform_hint else []),
                            extra_reason=auto_reason,
                        )

                done_message = "completed_with_warnings" if job.warnings else "completed"
                job.error = None
                job.mark("done", message=done_message, result=result)
            except Exception as exc:
                if job is None:
                    job = JobRecord.queued(ingest)
                job.ensure_tracking_fields()
                if current_phase:
                    job.set_phase(current_phase, "failed")
                job.mark("failed", message="failed", error=str(exc))
            finally:
                if job is not None and evidence is not None:
                    cleanup_errors = self._cleanup_temp_artifacts(ingest.request_id, evidence)
                    for item in cleanup_errors:
                        job.add_warning(f"cleanup_error: {item}")
                if job is not None:
                    self.jobs.save(job)
                self._queue.task_done()

    def _cleanup_temp_artifacts(self, request_id: str, evidence) -> list[str]:
        errors: list[str] = []
        metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
        temp_files: set[str] = set()
        temp_dirs: set[str] = set()

        raw_temp_files = metadata.get("temp_image_refs", [])
        if isinstance(raw_temp_files, list):
            for item in raw_temp_files:
                value = str(item).strip()
                if value:
                    temp_files.add(value)

        raw_temp_dirs = metadata.get("temp_artifact_dirs", [])
        if isinstance(raw_temp_dirs, list):
            for item in raw_temp_dirs:
                value = str(item).strip()
                if value:
                    temp_dirs.add(value)

        request_artifacts_dir = (self.base_state_dir / "artifacts" / request_id).resolve()
        temp_dirs.add(str(request_artifacts_dir))

        for raw in sorted(temp_files):
            path = Path(raw).expanduser()
            try:
                if path.exists() and path.is_file():
                    path.unlink()
            except OSError as exc:
                errors.append(f"{path}: {exc}")

        for raw in sorted(temp_dirs):
            path = Path(raw).expanduser()
            try:
                if path.exists() and path.is_dir():
                    shutil.rmtree(path)
            except OSError as exc:
                errors.append(f"{path}: {exc}")
        return errors
