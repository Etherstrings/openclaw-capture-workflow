"""Evidence quality gates and refusal helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from .config import AppConfig
from .evidence_utils import collect_timeline_segments, speech_items
from .models import EvidenceBundle, SummaryResult


@dataclass
class QualityGateResult:
    source_kind: str
    outcome: str
    mode: str = "full"
    reasons: list[str] = field(default_factory=list)
    refusal_reason: str = ""
    speech_chars: int = 0
    speech_seconds: float = 0.0
    duration_seconds: float | None = None
    coverage_ratio: float | None = None
    primary_sources: list[str] = field(default_factory=list)
    has_visual_support: bool = False
    speech_quality: str = "good"
    speech_quality_reasons: list[str] = field(default_factory=list)
    theme_only: bool = False
    probe_quality: str = "weak"
    expected_outline_count: int = 0
    observed_outline_count: int = 0
    retained_outline_count: int = 0
    chunk_count: int = 0
    chunk_coverage_ratio: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _estimate_covered_speech_seconds(segments: list[dict[str, Any]]) -> float:
    covered = 0.0
    sorted_segments = sorted(
        [item for item in segments if isinstance(item.get("start"), (int, float))],
        key=lambda item: float(item.get("start") or 0.0),
    )
    for index, item in enumerate(sorted_segments):
        start = float(item.get("start") or 0.0)
        end = item.get("end")
        if not isinstance(end, (int, float)):
            if index + 1 < len(sorted_segments):
                end = float(sorted_segments[index + 1].get("start") or start + 6.0)
            else:
                end = start + min(12.0, max(4.0, len(str(item.get("text", "")).strip()) / 12.0))
        end_value = max(start, float(end))
        covered += max(0.0, end_value - start)
    return round(covered, 3)


def _video_duration_seconds(evidence: EvidenceBundle) -> float | None:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    candidates = [
        metadata.get("video_duration_seconds"),
        metadata.get("bilibili_duration_seconds"),
    ]
    for value in candidates:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _gate_mode(evidence: EvidenceBundle) -> str:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    profile = str(metadata.get("video_extraction_profile", "")).strip().lower()
    if "probe" in profile:
        return "probe"
    if metadata.get("video_probe_seconds"):
        return "probe"
    return "full"


_CN_NUM_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "十六": 16,
    "十七": 17,
    "十八": 18,
    "十九": 19,
    "二十": 20,
}

_COMMON_EN_STOPWORDS = {
    "the", "and", "that", "with", "from", "this", "were", "when", "into", "they", "their", "there", "which", "have",
    "will", "would", "about", "while", "where", "what", "your", "just", "like", "than", "then", "them", "been",
    "were", "also", "could", "should", "only", "because", "other", "these", "those", "humans", "human", "story",
    "first", "history", "world", "begin", "begins", "around", "exist", "species",
}


def _parse_count_token(token: str) -> int | None:
    value = str(token or "").strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    return _CN_NUM_MAP.get(value)


def _expected_outline_count(evidence: EvidenceBundle) -> int:
    corpus = "\n".join(
        [
            str(evidence.title or ""),
            str(evidence.text or "")[:3000],
            str(evidence.transcript or "")[:3000],
        ]
    )
    patterns = [
        re.compile(r"\btop\s*(\d{1,2})\b", re.IGNORECASE),
        re.compile(r"(\d{1,2}|[一二三四五六七八九十两]{1,3})\s*(?:个|种|类|条|问|点|步|章|招|名|大)"),
        re.compile(r"(十大|十个|十二个|十条|十二条|十问|十二问)"),
    ]
    counts: list[int] = []
    for pattern in patterns:
        for match in pattern.findall(corpus):
            token = match if isinstance(match, str) else match[0]
            if token == "十大":
                counts.append(10)
                continue
            count = _parse_count_token(str(token).replace("两", "二"))
            if count is not None and 2 <= count <= 30:
                counts.append(count)
    return max(counts, default=0)


def _observed_outline_count(evidence: EvidenceBundle) -> int:
    corpus = "\n".join(
        [
            str(evidence.text or ""),
            str(evidence.transcript or ""),
        ]
    )
    patterns = [
        re.compile(r"问题([一二三四五六七八九十\d]{1,3})"),
        re.compile(r"第([一二三四五六七八九十\d]{1,3})名"),
        re.compile(r"(\d{1,2})[.)、]\s*"),
    ]
    seen: set[int] = set()
    for pattern in patterns:
        for match in pattern.findall(corpus):
            token = match if isinstance(match, str) else match[0]
            count = _parse_count_token(str(token))
            if count is not None and 1 <= count <= 50:
                seen.add(count)
    return len(seen)


def _analyze_speech_quality(evidence: EvidenceBundle, speech_corpus: str, primary_sources: list[str]) -> tuple[str, list[str]]:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    transcript_language = str(metadata.get("transcript_language") or metadata.get("subtitle_language") or "").strip().lower()
    text = re.sub(r"\s+", " ", speech_corpus or "").strip()
    if not text:
        return "bad", ["speech corpus empty"]

    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    latin_tokens = re.findall(r"[A-Za-z']+", text.lower())
    token_count = len(latin_tokens)
    stopword_ratio = 0.0
    long_word_ratio = 0.0
    if token_count:
        stopword_ratio = sum(1 for token in latin_tokens if token in _COMMON_EN_STOPWORDS) / token_count
        long_word_ratio = sum(1 for token in latin_tokens if len(token) >= 10) / token_count

    reasons: list[str] = []
    quality = "good"
    mostly_latin = latin_chars > max(60, cjk_chars * 3)
    if transcript_language.startswith("zh") and mostly_latin:
        reasons.append("speech language mismatch with transcript metadata")
    if mostly_latin and token_count >= 20:
        if stopword_ratio < 0.05:
            reasons.append("speech transcript looks low quality for English")
        if long_word_ratio > 0.38:
            reasons.append("speech transcript contains too many long noisy tokens")

    if reasons and "subtitle" not in primary_sources:
        quality = "bad"
    elif reasons:
        quality = "degraded"
    return quality, reasons


def evaluate_quality_gate(evidence: EvidenceBundle, config: AppConfig) -> QualityGateResult:
    if evidence.source_kind != "video_url":
        sufficient = bool((evidence.merged_text or evidence.text or "").strip())
        return QualityGateResult(
            source_kind=evidence.source_kind,
            mode="full",
            outcome="summarized" if sufficient else "refused",
            reasons=[] if sufficient else ["missing textual evidence"],
            refusal_reason="" if sufficient else "未拿到可验证正文，当前拒绝生成内容结论。",
        )

    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    tracks = metadata.get("tracks", {}) if isinstance(metadata.get("tracks"), dict) else {}
    mode = _gate_mode(evidence)
    probe_seconds = 0.0
    try:
        probe_seconds = float(metadata.get("video_probe_seconds") or 0.0)
    except (TypeError, ValueError):
        probe_seconds = 0.0
    speech = speech_items(evidence)
    if not speech:
        if tracks.get("has_subtitle") and (evidence.text or "").strip():
            speech_chars = len((evidence.text or "").strip())
            primary_sources = ["subtitle"]
        elif tracks.get("has_transcript") and ((evidence.transcript or "").strip() or (evidence.text or "").strip()):
            fallback_text = (evidence.transcript or evidence.text or "").strip()
            speech_chars = len(fallback_text)
            primary_sources = ["asr"]
        else:
            speech_chars = 0
            primary_sources = []
    else:
        speech_chars = sum(len(item.text.strip()) for item in speech)
        primary_sources = sorted({item.source for item in speech if item.is_primary})
        if not primary_sources:
            primary_sources = sorted({item.source for item in speech})
    has_visual = any(bool(item.text.strip()) for item in evidence.evidence_items if item.source in {"keyframes", "keyframe_ocr"})
    if not has_visual:
        has_visual = bool(tracks.get("has_keyframes") or tracks.get("has_keyframe_ocr"))
    segments = collect_timeline_segments(evidence)
    speech_seconds = _estimate_covered_speech_seconds(segments)
    duration_seconds = _video_duration_seconds(evidence)
    coverage_ratio = None
    if duration_seconds and duration_seconds > 0:
        coverage_ratio = round(min(1.0, speech_seconds / duration_seconds), 4)

    min_refuse_chars = max(1, int(config.video_accuracy.min_speech_chars_refuse))
    min_summary_chars = max(min_refuse_chars, int(config.video_accuracy.min_speech_chars_summarize))
    min_ratio = float(config.video_accuracy.min_coverage_ratio_for_summary)
    speech_corpus = "\n".join(item.text.strip() for item in speech) if speech else (evidence.transcript or evidence.text or "").strip()
    speech_quality, speech_quality_reasons = _analyze_speech_quality(evidence, speech_corpus, primary_sources)
    expected_outline_count = _expected_outline_count(evidence)
    observed_outline_count = _observed_outline_count(evidence)
    chunk_count = 0
    if duration_seconds and duration_seconds > 0:
        chunk_count = max(1, int((speech_seconds + max(0, int(config.video_summary.chunk_overlap_seconds))) // max(1, int(config.video_summary.chunk_window_seconds))))
    chunk_coverage_ratio = coverage_ratio
    probe_quality = "weak"
    if speech_chars >= 900 or speech_seconds >= 90 or (expected_outline_count > 0 and observed_outline_count >= max(3, min(expected_outline_count, 5))):
        probe_quality = "strong"
    elif speech_chars >= 550 or speech_seconds >= 45 or observed_outline_count >= 1:
        probe_quality = "usable"

    reasons: list[str] = []
    if not primary_sources:
        reasons.append("missing speech evidence (subtitle/asr)")
    elif speech_chars < min_refuse_chars:
        reasons.append(f"speech evidence too short (<{min_refuse_chars} chars)")

    if reasons:
        return QualityGateResult(
            source_kind="video_url",
            mode=mode,
            outcome="refused",
            reasons=reasons,
            refusal_reason="未取得可验证语音证据，拒绝编写视频内容结论。",
            speech_chars=speech_chars,
            speech_seconds=speech_seconds,
            duration_seconds=duration_seconds,
            coverage_ratio=coverage_ratio,
            primary_sources=primary_sources,
            has_visual_support=has_visual,
            speech_quality=speech_quality,
            speech_quality_reasons=speech_quality_reasons,
            expected_outline_count=expected_outline_count,
            observed_outline_count=observed_outline_count,
            probe_quality=probe_quality,
            chunk_count=chunk_count,
            chunk_coverage_ratio=chunk_coverage_ratio,
        )

    partial_reasons: list[str] = []
    theme_only = False
    if mode == "probe":
        if speech_quality == "bad" and "subtitle" not in primary_sources:
            partial_reasons.append("speech transcript quality too low for detailed summary")
            theme_only = True
        if expected_outline_count > 0 and observed_outline_count > 0 and observed_outline_count < expected_outline_count:
            partial_reasons.append(f"enumeration coverage incomplete ({observed_outline_count}/{expected_outline_count})")
        if duration_seconds and duration_seconds > 0:
            reference = probe_seconds if probe_seconds > 0 else min(duration_seconds, float(config.video_summary.chunk_window_seconds))
            captured = max(speech_seconds, 0.0)
            if reference > 0 and captured < max(30.0, reference * 0.5):
                partial_reasons.append(f"probe coverage limited ({round(captured,1)}s/{round(duration_seconds,1)}s)")
        else:
            partial_reasons.append("probe duration unknown")
        if probe_quality == "weak":
            partial_reasons.append("probe evidence still shallow")
        if not partial_reasons and probe_quality == "weak":
            partial_reasons.append("probe evidence still shallow")
        if duration_seconds and duration_seconds > 0 and probe_seconds >= duration_seconds * 0.8 and speech_chars >= max(min_refuse_chars, 700) and speech_quality != "bad":
            return QualityGateResult(
                source_kind="video_url",
                mode=mode,
                outcome="summarized",
                reasons=[],
                refusal_reason="",
                speech_chars=speech_chars,
                speech_seconds=speech_seconds,
                duration_seconds=duration_seconds,
                coverage_ratio=coverage_ratio,
                primary_sources=primary_sources,
                has_visual_support=has_visual,
                speech_quality=speech_quality,
                speech_quality_reasons=speech_quality_reasons,
                theme_only=False,
                probe_quality=probe_quality,
                expected_outline_count=expected_outline_count,
                observed_outline_count=observed_outline_count,
                chunk_count=chunk_count,
                chunk_coverage_ratio=chunk_coverage_ratio,
            )
    else:
        if speech_chars < min_summary_chars:
            partial_reasons.append(f"speech evidence below summary threshold (<{min_summary_chars} chars)")
        if coverage_ratio is None:
            partial_reasons.append("video duration unknown")
        elif coverage_ratio < min_ratio:
            partial_reasons.append(f"speech coverage ratio below threshold (<{min_ratio:.2f})")
        if speech_quality == "bad" and "subtitle" not in primary_sources:
            partial_reasons.append("speech transcript quality too low for detailed summary")
            theme_only = True
        if expected_outline_count > 0 and observed_outline_count > 0 and observed_outline_count < expected_outline_count:
            partial_reasons.append(f"enumeration coverage incomplete ({observed_outline_count}/{expected_outline_count})")

    if partial_reasons:
        return QualityGateResult(
            source_kind="video_url",
            mode=mode,
            outcome="partial",
            reasons=partial_reasons,
            refusal_reason="",
            speech_chars=speech_chars,
            speech_seconds=speech_seconds,
            duration_seconds=duration_seconds,
            coverage_ratio=coverage_ratio,
            primary_sources=primary_sources,
            has_visual_support=has_visual,
            speech_quality=speech_quality,
            speech_quality_reasons=speech_quality_reasons,
            theme_only=theme_only,
            probe_quality=probe_quality,
            expected_outline_count=expected_outline_count,
            observed_outline_count=observed_outline_count,
            chunk_count=chunk_count,
            chunk_coverage_ratio=chunk_coverage_ratio,
        )

    return QualityGateResult(
        source_kind="video_url",
        mode=mode,
        outcome="summarized",
        reasons=[],
        refusal_reason="",
        speech_chars=speech_chars,
        speech_seconds=speech_seconds,
        duration_seconds=duration_seconds,
        coverage_ratio=coverage_ratio,
        primary_sources=primary_sources,
        has_visual_support=has_visual,
        speech_quality=speech_quality,
        speech_quality_reasons=speech_quality_reasons,
        theme_only=False,
        probe_quality=probe_quality,
        expected_outline_count=expected_outline_count,
        observed_outline_count=observed_outline_count,
        chunk_count=chunk_count,
        chunk_coverage_ratio=chunk_coverage_ratio,
    )


def build_refusal_summary(evidence: EvidenceBundle, gate: QualityGateResult) -> SummaryResult:
    title = re.sub(r"\s+", " ", str(evidence.title or "视频内容拒绝总结").strip()) or "视频内容拒绝总结"
    reason = gate.refusal_reason or "当前证据不足，拒绝生成内容结论。"
    evidence_basis = [
        f"speech_chars={gate.speech_chars}",
    ]
    if gate.primary_sources:
        evidence_basis.append("speech_sources=" + ",".join(gate.primary_sources))
    if gate.duration_seconds:
        evidence_basis.append(f"duration_seconds={round(gate.duration_seconds, 3)}")
    if gate.coverage_ratio is not None:
        evidence_basis.append(f"coverage_ratio={round(gate.coverage_ratio, 4)}")
    uncertainties = [reason, *gate.reasons]
    return SummaryResult(
        title=title,
        primary_topic="视频",
        secondary_topics=[],
        entities=[],
        conclusion=reason,
        bullets=[
            "当前证据不足，不能可靠总结这条视频。",
            "原因: " + reason,
            *["证据缺口: " + item for item in gate.reasons[:2]],
        ],
        evidence_quotes=[],
        coverage="partial",
        confidence="low",
        note_tags=["video_refused"],
        follow_up_actions=["后续如需判断内容，需要先补到字幕或音频转写。"],
        timeliness="low",
        effectiveness="low",
        recommendation_level="skip",
        reader_judgment="当前证据不足，不建议继续基于这版结果判断内容。",
        outcome="refused",
        evidence_basis=evidence_basis,
        timeline_sections=[],
        uncertainties=uncertainties,
        refusal_reason=reason,
    )


def build_theme_only_summary(evidence: EvidenceBundle, gate: QualityGateResult) -> SummaryResult:
    title = re.sub(r"\s+", " ", str(evidence.title or "视频主题级总结").strip()) or "视频主题级总结"
    source_url = str(evidence.source_url or "").strip()
    basis = [f"speech_quality={gate.speech_quality}", f"speech_chars={gate.speech_chars}"]
    if gate.primary_sources:
        basis.append("speech_sources=" + ",".join(gate.primary_sources))
    if gate.expected_outline_count:
        basis.append(f"expected_outline_count={gate.expected_outline_count}")
    conclusion = f"当前只能确认视频主题与大致范围，但语音转写质量不足，不能可靠还原详细论点。"
    bullets = []
    if source_url:
        bullets.append(f"视频链接: {source_url}")
    if title:
        bullets.append(f"主题: {title}")
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    description = str(metadata.get("bilibili_description") or "").strip()
    if description:
        bullets.append("元数据简介: " + re.sub(r"\s+", " ", description)[:100])
    bullets = bullets[:3] or ["当前仅能确认视频主题。"]
    uncertainties = [
        "语音转写质量不足，当前不提供细节级总结。",
        *gate.speech_quality_reasons[:2],
        *gate.reasons[:2],
    ]
    return SummaryResult(
        title=title,
        primary_topic="视频",
        secondary_topics=[],
        entities=[],
        conclusion=conclusion,
        bullets=bullets,
        evidence_quotes=[],
        coverage="partial",
        confidence="low",
        note_tags=["video_theme_only"],
        follow_up_actions=["如果后续需要细节判断，先补官方字幕或更高质量转写。"],
        timeliness="low",
        effectiveness="low",
        recommendation_level="optional",
        reader_judgment="当前只能确认主题，不能把细节当作可信结论。",
        outcome="partial",
        evidence_basis=basis,
        timeline_sections=[],
        uncertainties=[item for item in uncertainties if item],
        refusal_reason="",
    )


def build_model_unavailable_summary(evidence: EvidenceBundle, gate: QualityGateResult, error: str) -> SummaryResult:
    title = re.sub(r"\s+", " ", str(evidence.title or "视频总结失败").strip()) or "视频总结失败"
    source_url = str(evidence.source_url or "").strip()
    cleaned_error = re.sub(r"\s+", " ", str(error or "").strip())
    bullets = []
    if source_url:
        bullets.append(f"视频链接: {source_url}")
    bullets.append("当前拿到了可验证证据，但视频总结模型不可用。")
    if gate.expected_outline_count:
        bullets.append(f"预计结构总项: {gate.expected_outline_count}")
    outcome = "partial" if gate.outcome != "refused" else "refused"
    conclusion = "当前视频总结模型不可用，已停止生成详细内容结论。"
    uncertainties = [
        "模型不可用，当前没有产出可依赖的细节总结。",
        cleaned_error,
        *gate.reasons[:2],
    ]
    return SummaryResult(
        title=title,
        primary_topic="视频",
        secondary_topics=[],
        entities=[],
        conclusion=conclusion,
        bullets=[item for item in bullets if item],
        evidence_quotes=[],
        coverage="partial",
        confidence="low",
        note_tags=["video_model_unavailable"],
        follow_up_actions=["如果后续需要细节总结，稍后重试视频总结模型。"],
        timeliness="low",
        effectiveness="low",
        recommendation_level="skip",
        reader_judgment="当前问题在总结模型不可用，这版结果不能给出可靠细节。",
        outcome=outcome,
        evidence_basis=[
            f"speech_chars={gate.speech_chars}",
            "speech_sources=" + ",".join(gate.primary_sources) if gate.primary_sources else "",
        ],
        timeline_sections=[],
        uncertainties=[item for item in uncertainties if item],
        refusal_reason=conclusion if outcome == "refused" else "",
    )
