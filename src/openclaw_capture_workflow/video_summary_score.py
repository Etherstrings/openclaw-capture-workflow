"""Video summary clarity scoring focused on "是否讲清视频在讲什么"."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any


_GENERIC_TOKENS = (
    "已提取核心事实",
    "帮助快速",
    "内容完整",
    "覆盖全面",
    "适合先留档",
    "值得继续",
    "对你有用",
)

_OVERCLAIM_TOKENS = (
    "完全覆盖",
    "完整覆盖",
    "无任何误差",
    "百分百准确",
    "无需回看",
)

_BOUNDARY_TOKENS = (
    "仅覆盖",
    "部分",
    "证据",
    "局限",
    "不完整",
    "不能当作",
    "建议回看",
    "缺口",
    "不建议直接",
)


@dataclass
class VideoClarityScore:
    topic_clarity: int
    mainline_coverage: int
    factual_fidelity: int
    boundary_honesty: int
    readability: int
    total: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _clean_lines(values: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(values, list):
        return []
    lines: list[str] = []
    for raw in values:
        text = _clean_text(raw).strip("。；;")
        if not text or text in lines:
            continue
        lines.append(text)
        if len(lines) >= limit:
            break
    return lines


def _clamp_0_2(value: int) -> int:
    return max(0, min(2, int(value)))


def score_video_summary(
    summary: dict[str, Any],
    *,
    outcome: str = "",
    quality_gate: dict[str, Any] | None = None,
    status: str = "done",
) -> VideoClarityScore:
    quality_gate = quality_gate if isinstance(quality_gate, dict) else {}
    outcome_norm = _clean_text(outcome or summary.get("outcome", "")).lower() or "partial"
    status_norm = _clean_text(status).lower()
    conclusion = _clean_text(summary.get("conclusion", ""))
    bullets = _clean_lines(summary.get("bullets", []), limit=10)
    uncertainties = _clean_lines(summary.get("uncertainties", []), limit=6)
    refusal_reason = _clean_text(summary.get("refusal_reason", ""))
    coverage = _clean_text(summary.get("coverage", "")).lower()
    note_tags = {str(item).strip() for item in summary.get("note_tags", []) if str(item).strip()} if isinstance(summary.get("note_tags"), list) else set()
    timeline_sections = summary.get("timeline_sections", []) if isinstance(summary.get("timeline_sections"), list) else []
    gate_reasons = [str(item) for item in quality_gate.get("reasons", [])] if isinstance(quality_gate.get("reasons"), list) else []
    expected_outline_count = int(quality_gate.get("expected_outline_count", 0) or 0)
    retained_outline_count = int(quality_gate.get("retained_outline_count", 0) or 0)

    notes: list[str] = []

    if status_norm != "done":
        notes.append("job 未完成，当前分数仅供参考。")

    # 1) 主题清晰度：是否直接讲清“这条视频到底在讲什么”
    topic_clarity = 0
    if outcome_norm == "refused":
        topic_clarity = 1 if refusal_reason else 0
    elif conclusion:
        if len(conclusion) >= 18 and not any(token in conclusion for token in _GENERIC_TOKENS):
            topic_clarity = 2
        else:
            topic_clarity = 1
    elif bullets:
        topic_clarity = 1
    if topic_clarity < 2:
        notes.append("主题句不够直接，首段建议更明确回答“视频在讲什么”。")

    # 2) 主线覆盖：是否抓住核心观点/流程，而非只拼碎片
    mainline_coverage = 0
    informative_bullets = [item for item in bullets if len(item) >= 8]
    if outcome_norm == "refused":
        mainline_coverage = 0
    elif len(informative_bullets) >= 3:
        mainline_coverage = 2
    elif len(informative_bullets) >= 1:
        mainline_coverage = 1
    if len(timeline_sections) >= 2 and len(informative_bullets) >= 2:
        mainline_coverage = max(mainline_coverage, 2)
    elif len(timeline_sections) >= 1:
        mainline_coverage = max(mainline_coverage, 1)
    if expected_outline_count > 0:
        ratio = retained_outline_count / max(1, expected_outline_count)
        if ratio < 0.35:
            mainline_coverage -= 1
            notes.append(f"主线覆盖偏低（枚举保留 {retained_outline_count}/{expected_outline_count}）。")
        elif ratio >= 0.7:
            mainline_coverage += 1
    mainline_coverage = _clamp_0_2(mainline_coverage)

    # 3) 事实忠实：避免证据不足时强总结，避免原始转写污染
    factual_fidelity = 2
    corpus = "\n".join([conclusion, *bullets]).lower()
    if outcome_norm != "refused":
        if coverage == "partial" and any(token in corpus for token in ("完整", "全面", "完全", "无遗漏")):
            factual_fidelity -= 2
            notes.append("证据 partial 但文案有过度完整化表达。")
        if any(len(item) > 140 for item in bullets[:3]):
            factual_fidelity -= 1
            notes.append("要点里存在过长句，疑似原始转写未充分压缩。")
        if any("missing speech track" in str(reason).lower() for reason in gate_reasons) and outcome_norm == "summarized":
            factual_fidelity -= 1
            notes.append("缺少语音主证据但仍给出完整总结。")
        if "video_model_unavailable" in note_tags and outcome_norm == "summarized":
            factual_fidelity -= 1
            notes.append("模型不可用标签与 summarized 结果不一致。")
    factual_fidelity = _clamp_0_2(factual_fidelity)

    # 4) 边界诚实：证据不足时要明确说清“哪里还不能信”
    boundary_honesty = 0
    if outcome_norm == "refused":
        boundary_honesty = 2 if refusal_reason else 1
    elif outcome_norm == "partial":
        boundary_corpus = "\n".join([conclusion, *bullets, *uncertainties])
        has_boundary = bool(uncertainties) or any(token in boundary_corpus for token in _BOUNDARY_TOKENS)
        boundary_honesty = 2 if has_boundary else 0
        if boundary_honesty == 0:
            notes.append("partial 结果没有清晰标注证据边界。")
    else:
        if any(token in corpus for token in _OVERCLAIM_TOKENS):
            boundary_honesty = 0
            notes.append("存在过度确定性表达。")
        else:
            boundary_honesty = 2

    # 5) 可读性：首屏是否能快速读懂，而不是模板/噪声
    readability = 0
    if conclusion or bullets:
        readability = 1
    if bullets:
        unique_ratio = len(set(bullets)) / max(1, len(bullets))
        avg_len = sum(len(item) for item in bullets) / max(1, len(bullets))
        generic_hits = sum(1 for item in bullets[:3] if any(token in item for token in _GENERIC_TOKENS))
        if unique_ratio >= 0.7 and 10 <= avg_len <= 85 and generic_hits == 0:
            readability = 2
        elif generic_hits >= 2:
            readability = 0
            notes.append("首屏要点过于模板化。")
    if len(conclusion) > 220:
        readability = max(0, readability - 1)
        notes.append("结论过长，建议前置压缩成 1-2 句。")
    readability = _clamp_0_2(readability)

    total = topic_clarity + mainline_coverage + factual_fidelity + boundary_honesty + readability
    return VideoClarityScore(
        topic_clarity=topic_clarity,
        mainline_coverage=mainline_coverage,
        factual_fidelity=factual_fidelity,
        boundary_honesty=boundary_honesty,
        readability=readability,
        total=total,
        notes=notes[:8],
    )


def score_video_summary_from_job_payload(job_payload: dict[str, Any]) -> dict[str, Any]:
    result = job_payload.get("result", {}) if isinstance(job_payload.get("result"), dict) else {}
    summary = result.get("summary", {}) if isinstance(result.get("summary"), dict) else {}
    score = score_video_summary(
        summary,
        outcome=str(result.get("outcome", "") or summary.get("outcome", "")),
        quality_gate=result.get("quality_gate", {}) if isinstance(result.get("quality_gate"), dict) else {},
        status=str(job_payload.get("status", "")),
    )
    return score.to_dict()
