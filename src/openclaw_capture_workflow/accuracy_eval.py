"""Accuracy evaluation module for end-to-end capture quality diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from .config import AppConfig, SummarizerConfig
from .extractor import EvidenceExtractor
from .models import EvidenceBundle, IngestRequest, SummaryResult
from .note_renderer import OpenAICompatibleNoteRenderer
from .obsidian import ObsidianWriter
from .processor import _build_fallback_summary
from .summarizer import OpenAICompatibleSummarizer


@dataclass
class EvalExpectation:
    required_keywords: List[str] = field(default_factory=list)
    required_links: List[str] = field(default_factory=list)
    required_projects: List[str] = field(default_factory=list)
    required_skill_ids: List[str] = field(default_factory=list)
    required_skills: List[str] = field(default_factory=list)
    required_actions: List[str] = field(default_factory=list)
    require_action_checklist: bool = False
    forbidden_phrases: List[str] = field(default_factory=list)
    min_evidence_chars: int = 80

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "EvalExpectation":
        return cls(
            required_keywords=[str(item) for item in payload.get("required_keywords", []) if str(item).strip()],
            required_links=[str(item) for item in payload.get("required_links", []) if str(item).strip()],
            required_projects=[str(item) for item in payload.get("required_projects", []) if str(item).strip()],
            required_skill_ids=[str(item) for item in payload.get("required_skill_ids", []) if str(item).strip()],
            required_skills=[str(item) for item in payload.get("required_skills", []) if str(item).strip()],
            required_actions=[str(item) for item in payload.get("required_actions", []) if str(item).strip()],
            require_action_checklist=bool(payload.get("require_action_checklist", False)),
            forbidden_phrases=[str(item) for item in payload.get("forbidden_phrases", []) if str(item).strip()],
            min_evidence_chars=max(1, int(payload.get("min_evidence_chars", 80))),
        )


@dataclass
class EvalCase:
    case_id: str
    source_kind: str
    source_url: Optional[str] = None
    raw_text: Optional[str] = None
    platform_hint: Optional[str] = None
    image_refs: List[str] = field(default_factory=list)
    video_probe_seconds: Optional[int] = None
    force_full_video: bool = False
    expect: EvalExpectation = field(default_factory=EvalExpectation)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "EvalCase":
        case_id = str(payload.get("id") or payload.get("case_id") or "").strip()
        if not case_id:
            raise ValueError("eval case missing `id`")
        source_kind = str(payload.get("source_kind") or "").strip()
        if not source_kind:
            raise ValueError(f"eval case `{case_id}` missing `source_kind`")
        expect = EvalExpectation.from_dict(payload.get("expect", {}))
        return cls(
            case_id=case_id,
            source_kind=source_kind,
            source_url=payload.get("source_url"),
            raw_text=payload.get("raw_text"),
            platform_hint=payload.get("platform_hint"),
            image_refs=[str(item) for item in payload.get("image_refs", []) if str(item).strip()],
            video_probe_seconds=payload.get("video_probe_seconds"),
            force_full_video=bool(payload.get("force_full_video", False)),
            expect=expect,
        )


@dataclass
class StepScore:
    score: float
    passed: bool
    missing: List[str] = field(default_factory=list)
    forbidden_hits: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(float(self.score), 4),
            "passed": bool(self.passed),
            "missing": list(self.missing),
            "forbidden_hits": list(self.forbidden_hits),
            "notes": list(self.notes),
        }


@dataclass
class JudgeResult:
    score: float
    root_cause: str
    missing_facts: List[str]
    hallucination_risks: List[str]
    explanation: str
    raw: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(float(self.score), 4),
            "root_cause": self.root_cause,
            "missing_facts": list(self.missing_facts),
            "hallucination_risks": list(self.hallucination_risks),
            "explanation": self.explanation,
            "raw": dict(self.raw),
        }


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _normalize_token(value: str) -> str:
    return _normalize_text(value).lower()


def _contains_value(corpus: str, target: str) -> bool:
    source = _normalize_token(corpus)
    query = _normalize_token(target)
    if not query:
        return True
    return query in source


def _collect_signals_text(evidence: EvidenceBundle) -> str:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
    lines: List[str] = []
    for key in ["projects", "links", "skills", "skill_ids", "commands", "hashtags"]:
        values = signals.get(key, [])
        if not isinstance(values, list):
            continue
        for item in values:
            text = str(item).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def _summary_corpus(summary: SummaryResult) -> str:
    parts = [
        summary.title,
        summary.conclusion,
        *summary.bullets,
        *summary.evidence_quotes,
    ]
    return "\n".join([str(item) for item in parts if str(item).strip()])


def _required_items(expect: EvalExpectation) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    for value in expect.required_keywords:
        pairs.append(("keyword", value))
    for value in expect.required_links:
        pairs.append(("link", value))
    for value in expect.required_projects:
        pairs.append(("project", value))
    for value in expect.required_skill_ids:
        pairs.append(("skill_id", value))
    for value in expect.required_skills:
        pairs.append(("skill", value))
    for value in expect.required_actions:
        pairs.append(("action", value))
    return pairs


def _evaluate_expected_recall(corpus: str, expect: EvalExpectation) -> Tuple[float, List[str]]:
    required = _required_items(expect)
    if not required:
        return 1.0, []
    missing: List[str] = []
    hit = 0
    for label, value in required:
        if _contains_value(corpus, value):
            hit += 1
        else:
            missing.append(f"{label}:{value}")
    return hit / len(required), missing


def _forbidden_hits(corpus: str, expect: EvalExpectation, extra_forbidden: Optional[List[str]] = None) -> List[str]:
    hits: List[str] = []
    merged = list(expect.forbidden_phrases)
    if extra_forbidden:
        merged.extend(extra_forbidden)
    for token in merged:
        if _contains_value(corpus, token):
            hits.append(token)
    return hits


def evaluate_extract_step(evidence: EvidenceBundle, expect: EvalExpectation) -> StepScore:
    evidence_text = _normalize_text(evidence.text)
    signals_text = _collect_signals_text(evidence)
    merged_corpus = evidence_text + "\n" + signals_text
    length_score = min(1.0, len(evidence_text) / max(expect.min_evidence_chars, 1))
    recall, missing = _evaluate_expected_recall(merged_corpus, expect)
    score = 0.35 * length_score + 0.65 * recall
    notes: List[str] = []
    if len(evidence_text) < expect.min_evidence_chars:
        notes.append(f"evidence_too_short:{len(evidence_text)}<{expect.min_evidence_chars}")
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    warnings = metadata.get("fetch_warnings", [])
    if isinstance(warnings, list) and warnings:
        notes.append("fetch_warning:" + str(warnings[0]))
    passed = score >= 0.75 and not missing
    return StepScore(score=score, passed=passed, missing=missing, notes=notes)


def evaluate_signal_step(evidence: EvidenceBundle, expect: EvalExpectation) -> StepScore:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
    signal_parts: List[str] = []
    for key in ["projects", "links", "skills", "skill_ids", "commands", "hashtags"]:
        values = signals.get(key, [])
        if isinstance(values, list):
            signal_parts.extend([str(item) for item in values if str(item).strip()])
    corpus = "\n".join(signal_parts)
    required = list(expect.required_projects) + list(expect.required_links) + list(expect.required_skill_ids) + list(
        expect.required_skills
    )
    if not required:
        score = 1.0 if signal_parts else 0.85
        return StepScore(score=score, passed=score >= 0.8, notes=["no_signal_expectation"])
    missing: List[str] = []
    hit = 0
    for item in required:
        if _contains_value(corpus, item):
            hit += 1
        else:
            missing.append(item)
    recall = hit / len(required)
    score = recall
    return StepScore(score=score, passed=score >= 0.8 and not missing, missing=missing)


def evaluate_summary_step(summary: SummaryResult, expect: EvalExpectation) -> StepScore:
    corpus = _summary_corpus(summary)
    if summary.follow_up_actions:
        corpus = corpus + "\n" + "\n".join([str(item) for item in summary.follow_up_actions])
    recall, missing = _evaluate_expected_recall(corpus, expect)
    generic_forbidden = ["已提取核心事实", "模型不可用", "帮助你快速理解", "内容完整", "覆盖全面"]
    forbidden = _forbidden_hits(corpus, expect, generic_forbidden)
    penalty = min(0.5, 0.2 * len(forbidden))
    score = max(0.0, recall - penalty)
    notes: List[str] = []
    if len(summary.bullets) < 3:
        notes.append("too_few_bullets")
        score = max(0.0, score - 0.15)
    if len(_normalize_text(summary.conclusion)) < 12:
        notes.append("conclusion_too_short")
        score = max(0.0, score - 0.15)
    if expect.require_action_checklist and len(summary.follow_up_actions) < 2:
        notes.append("missing_action_checklist")
        score = max(0.0, score - 0.2)
    passed = score >= 0.75 and not missing and not forbidden
    return StepScore(score=score, passed=passed, missing=missing, forbidden_hits=forbidden, notes=notes)


def evaluate_note_step(note_content: str, expect: EvalExpectation) -> StepScore:
    lines = [line.strip() for line in note_content.splitlines() if line.strip()]
    unique_ratio = (len(set(lines)) / len(lines)) if lines else 0.0
    duplication_penalty = 0.0
    notes: List[str] = []
    if unique_ratio < 0.72:
        duplication_penalty = min(0.35, (0.72 - unique_ratio) * 0.8)
        notes.append(f"duplicate_ratio_low:{round(unique_ratio, 3)}")
    coverage, missing = _evaluate_expected_recall(note_content, expect)
    length_score = min(1.0, len(_normalize_text(note_content)) / 240) if note_content.strip() else 0.0
    if expect.require_action_checklist and not re.search(r"/install-skill|执行|安装|验证|运行|步骤", note_content):
        missing.append("action:expected_procedure")
    forbidden = _forbidden_hits(
        note_content,
        expect,
        extra_forbidden=[
            "步骤细节",
            "图片与帧（后置）",
            "## 一句话总结",
            "## 文字脑图",
            "## 对你有什么用",
            "## 贾维斯判断",
            "## 项目与链接",
            "## 关联笔记",
            "## 核心事实",
            "## 执行清单",
            "## 关键证据",
            "## 专业解读",
            "## 可信度与局限",
            "## 关键词",
            "帮助你快速理解",
            "已提取核心事实",
            "对你有用",
            "推荐等级",
            "大厂程序员视角",
        ],
    )
    forbidden_penalty = min(0.4, 0.2 * len(forbidden))
    score = max(0.0, 0.55 * coverage + 0.30 * length_score + 0.15 * unique_ratio - duplication_penalty - forbidden_penalty)
    passed = score >= 0.7 and not missing and not forbidden
    return StepScore(score=score, passed=passed, missing=missing, forbidden_hits=forbidden, notes=notes)


def diagnose_root_cause(
    extract_step: StepScore,
    signal_step: StepScore,
    summary_step: StepScore,
    note_step: StepScore,
    *,
    summary_mode: str,
    summary_error: str,
    judge_root_cause: str = "",
) -> str:
    if summary_error and summary_mode != "model":
        return "summary_model"
    if not extract_step.passed:
        return "extract"
    if not signal_step.passed:
        return "signals"
    if not summary_step.passed:
        return "summary"
    if not note_step.passed:
        return "renderer"
    if judge_root_cause:
        return judge_root_cause
    return "pass"


def build_fix_suggestion(root_cause: str) -> str:
    mapping = {
        "extract": "抓取阶段丢信息：优先补抓取链路（页面正文、OCR、字幕/ASR）并复测。",
        "signals": "信号抽取丢关键信息：补 regex 规则（repo/skill_id/link）并加回归样例。",
        "summary": "总结阶段漏关键事实：升级总结模型或加强提示词约束，再做 case 回放。",
        "summary_model": "总结模型调用失败：先修 API 可用性，再用强模型重跑并检查成本。",
        "renderer": "渲染阶段引入噪声：调整模板裁剪规则，禁用低价值段落。",
        "pass": "当前链路达标，可继续扩充更多真实样本回归。",
    }
    return mapping.get(root_cause, mapping["pass"])


def _estimate_tokens(text: str) -> int:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return 0
    return max(1, int(len(compact) * 1.05))


def estimate_call_cost_usd(input_tokens: int, output_tokens: int, input_usd_per_million: float, output_usd_per_million: float) -> float:
    return (input_tokens / 1_000_000.0 * input_usd_per_million) + (output_tokens / 1_000_000.0 * output_usd_per_million)


def _judge_with_model(
    *,
    config: SummarizerConfig,
    model: str,
    evidence: EvidenceBundle,
    summary: SummaryResult,
    expect: EvalExpectation,
    max_evidence_chars: int = 8000,
) -> JudgeResult:
    prompt = """You are a strict QA evaluator for Chinese knowledge capture outputs.
Return strict JSON with keys:
score, root_cause, missing_facts, hallucination_risks, explanation

Rules:
- score must be integer 0-100.
- root_cause must be one of: pass, extract, signals, summary, renderer.
- missing_facts: facts expected but missing in summary/note.
- hallucination_risks: claims not well supported by evidence.
- Keep explanation concise and factual.
"""
    payload = {
        "model": model,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "expectation": asdict(expect),
                        "evidence_text": (evidence.text or "")[:max_evidence_chars],
                        "summary": summary.to_dict(),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    req = urlrequest.Request(
        url=f"{config.api_base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=config.timeout_seconds) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"judge request failed: {exc}") from exc
    try:
        raw = body["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"unexpected judge response: {body}") from exc
    score = float(parsed.get("score", 0))
    score = min(100.0, max(0.0, score))
    root = str(parsed.get("root_cause", "pass")).strip() or "pass"
    if root not in {"pass", "extract", "signals", "summary", "renderer"}:
        root = "pass"
    missing = [str(item) for item in parsed.get("missing_facts", []) if str(item).strip()]
    hallucinations = [str(item) for item in parsed.get("hallucination_risks", []) if str(item).strip()]
    explanation = str(parsed.get("explanation", "")).strip()
    return JudgeResult(
        score=score / 100.0,
        root_cause=root,
        missing_facts=missing,
        hallucination_risks=hallucinations,
        explanation=explanation,
        raw=parsed,
    )


def load_eval_cases(path: str) -> List[EvalCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("eval cases must be a JSON array")
    return [EvalCase.from_dict(item) for item in payload]


def _evaluate_single_case(
    *,
    case: EvalCase,
    request_id: str,
    extractor: EvidenceExtractor,
    writer: ObsidianWriter,
    summarizer: Optional[OpenAICompatibleSummarizer],
    summary_mode: str,
    enable_judge: bool,
    judge_config: Optional[SummarizerConfig],
    judge_model: str,
    summary_price_in: float,
    summary_price_out: float,
    judge_price_in: float,
    judge_price_out: float,
) -> Dict[str, Any]:
    ingest = IngestRequest(
        chat_id="-1",
        reply_to_message_id="1",
        request_id=request_id,
        source_kind=case.source_kind,
        source_url=case.source_url,
        raw_text=case.raw_text or case.source_url,
        image_refs=list(case.image_refs),
        platform_hint=case.platform_hint,
        dry_run=True,
        video_probe_seconds=case.video_probe_seconds,
        force_full_video=case.force_full_video,
    )

    evidence = extractor.extract(ingest)
    resolved_summary_mode = summary_mode
    summary_error = ""
    if summary_mode == "model":
        if summarizer is None:
            raise RuntimeError("summary_mode=model but summarizer is not configured")
        try:
            summary = summarizer.summarize(evidence)
        except Exception as exc:
            summary = _build_fallback_summary(evidence)
            resolved_summary_mode = "fallback_due_to_error"
            summary_error = str(exc)
    else:
        summary = _build_fallback_summary(evidence)

    note_preview = writer.preview(summary, evidence)
    note_content = str(note_preview.get("content", ""))

    extract_step = evaluate_extract_step(evidence, case.expect)
    signal_step = evaluate_signal_step(evidence, case.expect)
    summary_step = evaluate_summary_step(summary, case.expect)
    note_step = evaluate_note_step(note_content, case.expect)

    judge_result = None
    judge_error = ""
    if enable_judge:
        if judge_config is None:
            judge_error = "judge_config_missing"
        else:
            try:
                judge_result = _judge_with_model(
                    config=judge_config,
                    model=judge_model,
                    evidence=evidence,
                    summary=summary,
                    expect=case.expect,
                )
            except Exception as exc:
                judge_error = str(exc)

    base_score = (
        extract_step.score * 0.35
        + signal_step.score * 0.20
        + summary_step.score * 0.30
        + note_step.score * 0.15
    )
    if judge_result is not None:
        overall_score = base_score * 0.75 + judge_result.score * 0.25
    else:
        overall_score = base_score

    root_cause = diagnose_root_cause(
        extract_step=extract_step,
        signal_step=signal_step,
        summary_step=summary_step,
        note_step=note_step,
        summary_mode=resolved_summary_mode,
        summary_error=summary_error,
        judge_root_cause=judge_result.root_cause if judge_result else "",
    )

    summary_input_tokens = 0
    summary_output_tokens = 0
    summary_cost_usd = 0.0
    if summary_mode == "model":
        summary_input_tokens = _estimate_tokens(evidence.text) + 800
        summary_output_tokens = _estimate_tokens(json.dumps(summary.to_dict(), ensure_ascii=False))
        summary_cost_usd = estimate_call_cost_usd(
            summary_input_tokens,
            summary_output_tokens,
            summary_price_in,
            summary_price_out,
        )

    judge_input_tokens = 0
    judge_output_tokens = 0
    judge_cost_usd = 0.0
    if enable_judge and judge_result is not None:
        judge_input_tokens = _estimate_tokens(evidence.text[:8000]) + _estimate_tokens(json.dumps(summary.to_dict(), ensure_ascii=False))
        judge_output_tokens = _estimate_tokens(json.dumps(judge_result.raw, ensure_ascii=False))
        judge_cost_usd = estimate_call_cost_usd(
            judge_input_tokens,
            judge_output_tokens,
            judge_price_in,
            judge_price_out,
        )

    missing_union: List[str] = []
    for step in [extract_step, signal_step, summary_step, note_step]:
        for item in step.missing:
            if item not in missing_union:
                missing_union.append(item)

    forbidden_union: List[str] = []
    for step in [extract_step, signal_step, summary_step, note_step]:
        for item in step.forbidden_hits:
            if item not in forbidden_union:
                forbidden_union.append(item)

    passed = overall_score >= 0.78 and not missing_union and not forbidden_union and not summary_error
    if judge_result is not None and judge_result.score < 0.72:
        passed = False
    if judge_result is not None and judge_result.root_cause != "pass":
        passed = False

    result: Dict[str, Any] = {
        "case_id": case.case_id,
        "source_kind": case.source_kind,
        "source_url": case.source_url,
        "summary_mode": resolved_summary_mode,
        "summary_error": summary_error or None,
        "steps": {
            "extract": extract_step.to_dict(),
            "signals": signal_step.to_dict(),
            "summary": summary_step.to_dict(),
            "note": note_step.to_dict(),
        },
        "overall_score": round(overall_score, 4),
        "passed": passed,
        "root_cause": root_cause,
        "fix_suggestion": build_fix_suggestion(root_cause),
        "missing": missing_union,
        "forbidden_hits": forbidden_union,
        "cost": {
            "summary_input_tokens": summary_input_tokens,
            "summary_output_tokens": summary_output_tokens,
            "summary_cost_usd": round(summary_cost_usd, 6),
            "judge_input_tokens": judge_input_tokens,
            "judge_output_tokens": judge_output_tokens,
            "judge_cost_usd": round(judge_cost_usd, 6),
            "total_cost_usd": round(summary_cost_usd + judge_cost_usd, 6),
        },
        "preview": {
            "note_path": str(note_preview.get("note_path", "")),
            "title": str(note_preview.get("title", "")),
            "content": note_content,
        },
        "evidence_stats": {
            "chars": len(_normalize_text(evidence.text)),
            "coverage": evidence.coverage,
            "evidence_type": evidence.evidence_type,
            "has_signals": bool(_collect_signals_text(evidence)),
        },
    }
    if judge_result is not None:
        result["judge"] = judge_result.to_dict()
    if judge_error:
        result["judge_error"] = judge_error
    return result


def run_accuracy_eval(
    *,
    config_path: str,
    cases_path: str,
    summary_mode: str = "fallback",
    summary_model: str = "",
    enable_judge: bool = False,
    judge_model: str = "",
    judge_api_base_url: str = "",
    judge_api_key: str = "",
    summary_price_input_usd_per_million: float = 0.15,
    summary_price_output_usd_per_million: float = 0.60,
    judge_price_input_usd_per_million: float = 0.15,
    judge_price_output_usd_per_million: float = 0.60,
    max_cases: int = 0,
) -> Dict[str, Any]:
    if summary_mode not in {"fallback", "model"}:
        raise ValueError("summary_mode must be one of: fallback, model")
    config = AppConfig.load(config_path)
    base_dir = Path(config_path).resolve().parent
    state_dir = config.ensure_state_dirs(base_dir)

    cases = load_eval_cases(cases_path)
    if max_cases > 0:
        cases = cases[: max_cases]

    effective_summary_cfg = config.summarizer
    if summary_mode == "model" and summary_model.strip():
        effective_summary_cfg = SummarizerConfig(
            api_base_url=config.summarizer.api_base_url,
            api_key=config.summarizer.api_key,
            model=summary_model.strip(),
            timeout_seconds=config.summarizer.timeout_seconds,
        )
    extractor = EvidenceExtractor(config, state_dir / "artifacts")
    writer = ObsidianWriter(
        config.obsidian,
        renderer=OpenAICompatibleNoteRenderer(effective_summary_cfg),
        materials_root=state_dir / "materials",
    )
    summarizer = OpenAICompatibleSummarizer(effective_summary_cfg) if summary_mode == "model" else None

    judge_config = None
    if enable_judge:
        effective_model = judge_model.strip() or config.summarizer.model
        effective_api_base = judge_api_base_url.strip() or config.summarizer.api_base_url
        effective_api_key = judge_api_key.strip() or config.summarizer.api_key
        judge_model = effective_model
        judge_config = SummarizerConfig(
            api_base_url=effective_api_base,
            api_key=effective_api_key,
            model=effective_model,
            timeout_seconds=config.summarizer.timeout_seconds,
        )

    results: List[Dict[str, Any]] = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    preview_dir = state_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)

    for idx, case in enumerate(cases):
        request_id = f"acc-{case.case_id}-{timestamp}-{idx+1}"
        item = _evaluate_single_case(
            case=case,
            request_id=request_id,
            extractor=extractor,
            writer=writer,
            summarizer=summarizer,
            summary_mode=summary_mode,
            enable_judge=enable_judge,
            judge_config=judge_config,
            judge_model=judge_model,
            summary_price_in=summary_price_input_usd_per_million,
            summary_price_out=summary_price_output_usd_per_million,
            judge_price_in=judge_price_input_usd_per_million,
            judge_price_out=judge_price_output_usd_per_million,
        )
        preview_file = preview_dir / f"{request_id}.md"
        preview_file.write_text(str(item["preview"]["content"]), encoding="utf-8")
        item["preview"]["file"] = str(preview_file)
        # Keep report compact; preview is stored on disk.
        item["preview"].pop("content", None)
        results.append(item)

    total_cost = round(sum(float(item["cost"]["total_cost_usd"]) for item in results), 6)
    pass_count = sum(1 for item in results if item.get("passed"))
    root_stats: Dict[str, int] = {}
    for item in results:
        root = str(item.get("root_cause", "pass"))
        root_stats[root] = root_stats.get(root, 0) + 1

    report: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config_path": str(Path(config_path).resolve()),
        "cases_path": str(Path(cases_path).resolve()),
        "summary_mode": summary_mode,
        "summary_model": effective_summary_cfg.model if summary_mode == "model" else "",
        "enable_judge": enable_judge,
        "judge_model": judge_model if enable_judge else "",
        "case_count": len(results),
        "pass_count": pass_count,
        "pass_rate": round((pass_count / len(results)) if results else 0.0, 4),
        "total_cost_usd": total_cost,
        "root_cause_stats": root_stats,
        "results": results,
    }
    return report


def render_markdown_report(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Accuracy Eval Report")
    lines.append("")
    lines.append(f"- 生成时间: {report.get('generated_at', '')}")
    lines.append(f"- 用例数: {report.get('case_count', 0)}")
    lines.append(f"- 通过数: {report.get('pass_count', 0)}")
    lines.append(f"- 通过率: {report.get('pass_rate', 0)}")
    lines.append(f"- 总成本(USD): {report.get('total_cost_usd', 0)}")
    lines.append("")
    lines.append("## 结果总览")
    lines.append("")
    lines.append("| case_id | pass | score | root_cause | summary_mode | cost_usd |")
    lines.append("|---|---:|---:|---|---|---:|")
    for item in report.get("results", []):
        lines.append(
            f"| {item.get('case_id','')} | {str(item.get('passed', False)).lower()} | "
            f"{item.get('overall_score', 0)} | {item.get('root_cause','')} | "
            f"{item.get('summary_mode','')} | {item.get('cost', {}).get('total_cost_usd', 0)} |"
        )
    lines.append("")
    lines.append("## 失败明细")
    lines.append("")
    failures = [item for item in report.get("results", []) if not item.get("passed")]
    if not failures:
        lines.append("- 无失败用例。")
        return "\n".join(lines) + "\n"
    for item in failures:
        lines.append(f"### {item.get('case_id', '')}")
        lines.append("")
        lines.append(f"- root_cause: {item.get('root_cause', '')}")
        lines.append(f"- suggestion: {item.get('fix_suggestion', '')}")
        missing = item.get("missing", [])
        if missing:
            lines.append("- missing:")
            for value in missing:
                lines.append(f"  - {value}")
        forbidden = item.get("forbidden_hits", [])
        if forbidden:
            lines.append("- forbidden_hits:")
            for value in forbidden:
                lines.append(f"  - {value}")
        if item.get("summary_error"):
            lines.append(f"- summary_error: {item.get('summary_error')}")
        if item.get("judge_error"):
            lines.append(f"- judge_error: {item.get('judge_error')}")
        judge = item.get("judge", {})
        if isinstance(judge, dict) and judge:
            lines.append(f"- judge_score: {judge.get('score', '')}")
            if judge.get("missing_facts"):
                lines.append("- judge_missing_facts:")
                for value in judge.get("missing_facts", []):
                    lines.append(f"  - {value}")
            if judge.get("hallucination_risks"):
                lines.append("- judge_hallucination_risks:")
                for value in judge.get("hallucination_risks", []):
                    lines.append(f"  - {value}")
            if judge.get("explanation"):
                lines.append(f"- judge_explanation: {judge.get('explanation')}")
        lines.append(f"- preview_file: {item.get('preview', {}).get('file', '')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def save_accuracy_report(report: Dict[str, Any], output_dir: Path, name_prefix: str = "accuracy_eval") -> Dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"{name_prefix}_{stamp}.json"
    md_path = output_dir / f"{name_prefix}_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}
