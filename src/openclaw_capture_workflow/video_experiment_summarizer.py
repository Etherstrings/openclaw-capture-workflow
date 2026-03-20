"""Gemini-focused video summary experiments via AiHubMix."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from .config import VideoSummaryConfig
from .evidence_utils import collect_timeline_segments
from .models import EvidenceBundle, SummaryResult
from .quality_gate import _expected_outline_count, _observed_outline_count
from .summarizer import PROMPT, _build_video_prompt_context, _extract_enumerated_points_from_text, _validate_and_normalize_summary
from .video_truth_eval import compute_retained_outline_count


class AiHubMixGeminiSummarizer:
    def __init__(self, config: VideoSummaryConfig) -> None:
        self.config = config

    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
        errors: list[str] = []
        for model in [self.config.model, self.config.fallback_model]:
            try:
                raw = self._request(model, evidence)
                summary = SummaryResult.from_json(raw)
                return _validate_and_normalize_summary(summary, evidence)
            except Exception as exc:
                errors.append(f"{model}:{exc}")
        raise RuntimeError("gemini summarizer failed: " + " | ".join(errors))

    def request_json_payload(self, system_prompt: str, payload: dict[str, Any]) -> str:
        errors: list[str] = []
        for model in [self.config.model, self.config.fallback_model]:
            try:
                return self._request_payload(model, system_prompt, payload)
            except Exception as exc:
                errors.append(f"{model}:{exc}")
        raise RuntimeError("gemini payload request failed: " + " | ".join(errors))

    def _request(self, model: str, evidence: EvidenceBundle) -> str:
        if (self.config.transport or "openai_compat").strip() == "native":
            return self._request_native(model, evidence)
        return self._request_openai_compat(model, evidence)

    def _request_payload(self, model: str, system_prompt: str, payload: dict[str, Any]) -> str:
        if (self.config.transport or "openai_compat").strip() == "native":
            return self._request_native_payload(model, system_prompt, payload)
        return self._request_openai_compat_payload(model, system_prompt, payload)

    def _request_openai_compat(self, model: str, evidence: EvidenceBundle) -> str:
        video_context = _build_video_prompt_context(evidence)
        payload = {
            "model": model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "source_kind": evidence.source_kind,
                            "source_url": evidence.source_url,
                            "platform_hint": evidence.platform_hint,
                            "title": evidence.title,
                            "evidence_type": evidence.evidence_type,
                            "coverage": evidence.coverage,
                            "text": evidence.text,
                            "transcript": evidence.transcript,
                            "metadata": evidence.metadata,
                            **video_context,
                        },
                        ensure_ascii=False,
                    ),
                },
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
            raise RuntimeError(f"gemini compat request failed: {exc}") from exc
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"unexpected gemini compat response: {body}") from exc

    def _request_openai_compat_payload(self, model: str, system_prompt: str, payload: dict[str, Any]) -> str:
        req_payload = {
            "model": model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        req = urlrequest.Request(
            url=f"{self.config.api_base_url.rstrip('/')}/chat/completions",
            data=json.dumps(req_payload).encode("utf-8"),
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
            raise RuntimeError(f"gemini compat request failed: {exc}") from exc
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"unexpected gemini compat response: {body}") from exc

    def _request_native(self, model: str, evidence: EvidenceBundle) -> str:
        base = self.config.api_base_url.rstrip("/")
        if not base.endswith("/gemini"):
            base = base + "/gemini"
        video_context = _build_video_prompt_context(evidence)
        payload = {
            "system_instruction": {"parts": [{"text": PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "source_kind": evidence.source_kind,
                                    "source_url": evidence.source_url,
                                    "platform_hint": evidence.platform_hint,
                                    "title": evidence.title,
                                    "evidence_type": evidence.evidence_type,
                                    "coverage": evidence.coverage,
                                    "text": evidence.text,
                                    "transcript": evidence.transcript,
                                    "metadata": evidence.metadata,
                                    **video_context,
                                },
                                ensure_ascii=False,
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        req = urlrequest.Request(
            url=f"{base}/v1beta/models/{model}:generateContent?key={self.config.api_key}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"gemini native request failed: {exc}") from exc
        try:
            return body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected gemini native response: {body}") from exc

    def _request_native_payload(self, model: str, system_prompt: str, payload: dict[str, Any]) -> str:
        base = self.config.api_base_url.rstrip("/")
        if not base.endswith("/gemini"):
            base = base + "/gemini"
        req_payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": json.dumps(payload, ensure_ascii=False)}],
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        req = urlrequest.Request(
            url=f"{base}/v1beta/models/{model}:generateContent?key={self.config.api_key}",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"gemini native request failed: {exc}") from exc
        try:
            return body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected gemini native response: {body}") from exc


CHUNK_SUMMARY_PROMPT = """Role: Video Evidence Reconstructor

You are given one timeline chunk from a real video transcript/subtitle stream.
Only use facts present in the chunk.
Return strict JSON with keys:
start, end, heading, summary, key_points, evidence_quotes, uncertainties

Rules:
- Write in Chinese.
- heading must be short and semantic.
- summary must be one sentence.
- key_points must contain 2 to 4 short factual bullets.
- evidence_quotes must be short verbatim fragments from the chunk.
- uncertainties must be empty unless the chunk is obviously noisy or incomplete.
- Never infer facts beyond the chunk.
"""


FINAL_VIDEO_SUMMARY_PROMPT = """Role: Evidence-Driven Video Analyst

You are given chunk summaries built from real subtitle/ASR evidence.
Return strict JSON with keys:
title, primary_topic, secondary_topics, entities, conclusion, bullets,
evidence_quotes, coverage, confidence, note_tags, follow_up_actions,
timeliness, effectiveness, recommendation_level, reader_judgment,
outcome, evidence_basis, timeline_sections, uncertainties, refusal_reason,
finance_matrix, finance_snapshot

Rules:
- Write in Chinese.
- Only use facts supported by chunk summaries and evidence basis.
- outcome must be summarized or partial.
- timeline_sections must preserve time order and include start, end, heading, summary, bullets, evidence.
- conclusion must directly answer what the video is really about.
- bullets should capture the most important 3 to 6 findings across the whole video.
- bullets must be concise factual findings, not a transcript recap and not a timeline dump.
- do not copy long raw ASR sentences or chunk text into bullets.
- for long videos, prioritize: core claim, what matters, concrete actions/choices, and boundary/risk.
- evidence_basis should list the evidence tracks and coverage basis.
- uncertainties should explicitly mention any coverage gaps or noisy sections.
- refusal_reason must be empty.
- Do not invent claims from metadata alone.
- If the video is not clearly about investments/holdings, return finance_matrix=[] and finance_snapshot={}.
- Only populate finance_matrix when the evidence explicitly supports concrete holdings, sectors, thesis, or risk statements.
- finance_matrix rows must use keys: name, sector, thesis, position_change, risk.
- finance_snapshot may only use keys: market_view, performance_review, action_plan.
- for finance videos, bullets should summarize the investment framework, market view, holding changes, and risk boundary rather than list timestamps.
"""


FINANCE_VIDEO_STRUCT_PROMPT = """Role: Finance Video Structurer

You are given chunk summaries from a Chinese investment/holding video.
Return strict JSON with keys:
finance_matrix, finance_snapshot

Rules:
- Write in Chinese.
- Only use facts explicitly supported by the chunk summaries or transcript excerpt.
- finance_matrix must be a list of rows with keys: name, sector, thesis, position_change, risk.
- Omit rows when the company/entity name is unclear or the evidence is too weak.
- finance_snapshot must be an object that may contain market_view, performance_review, action_plan.
- Each finance_snapshot field should be a short list of 1 to 4 factual lines.
- Do not invent prices, positions, or stock names.
"""


_HEURISTIC_FINANCE_CHUNK_HEADINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("投资哲学与策略框架", ("投资", "普通", "认知", "能力", "鄙视链", "赚钱", "体系", "哲学", "思路", "取之有道")),
    ("估值框架与持有逻辑", ("估值", "现金流", "股息", "持有", "压票", "便宜", "市盈率", "价值投资", "低估", "高估")),
    ("市场表现与组合回顾", ("指数", "恒指", "恒生", "回血", "回撤", "收益", "跑赢", "赚钱效应", "组合", "账单")),
    ("标的复盘与取舍", ("海底捞", "中国食品", "腾讯", "片仔癀", "中国海洋石油", "中远海控", "宁德时代", "福寿园", "华润医药", "太古")),
    ("风险控制与后续计划", ("风险", "后续", "计划", "调仓", "不追高", "防御", "回避", "仓位", "卖出", "买入")),
)

_HEURISTIC_GENERAL_CHUNK_HEADINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("开场背景与问题定义", ("开始", "今天", "背景", "为什么", "先说", "开场")),
    ("核心观点与主要判断", ("核心", "重点", "判断", "看法", "认为", "结论")),
    ("方法拆解与操作重点", ("方法", "步骤", "流程", "操作", "怎么", "拆解")),
    ("案例细节与补充说明", ("案例", "举例", "比如", "示例", "细节", "说明")),
    ("总结与后续建议", ("总结", "最后", "建议", "后续", "计划", "提醒")),
)

_FINANCE_ENTITY_ALIASES: dict[str, dict[str, Any]] = {
    "海底捞": {"aliases": ["海底捞"], "sector": "消费/餐饮"},
    "中国食品": {"aliases": ["中国食品"], "sector": "消费/饮料"},
    "腾讯控股": {"aliases": ["腾讯控股", "腾讯"], "sector": "互联网/平台"},
    "中国海洋石油": {"aliases": ["中国海洋石油", "中海油"], "sector": "能源/石油"},
    "中远海控": {"aliases": ["中远海控"], "sector": "航运"},
    "片仔癀": {"aliases": ["片仔癀", "骗仔癀"], "sector": "医药/中药"},
    "宁德时代": {"aliases": ["宁德时代", "内德时代", "宁的时代"], "sector": "新能源"},
    "福寿园": {"aliases": ["福寿园", "服生员"], "sector": "公用/殡葬服务"},
    "华润医药": {"aliases": ["华润医药", "华人医药"], "sector": "医药"},
    "太古股份公司": {"aliases": ["太古股份公司", "特股份公司", "泰股份公司"], "sector": "地产/综合"},
    "太古地产": {"aliases": ["太古地产", "太古地坛"], "sector": "地产"},
}

_FINANCE_ENTITY_PATTERN = re.compile(
    r"(中国[\u4e00-\u9fff]{1,6}(?:石油|海控|食品|医药|地产|银行|移动|电信|神华|海洋石油)"
    r"|中远[\u4e00-\u9fff]{1,4}"
    r"|华润[\u4e00-\u9fff]{1,4}"
    r"|宁德时代|福寿园|海底捞|腾讯控股|腾讯|片仔癀|骗仔癀|太古股份公司|太古股份|太古地产)"
)

_FINANCE_ENTITY_STOPWORDS = {"银行股", "大金融股", "市场先生", "普通投资者", "上市公司"}


def _format_seconds_label(value: float) -> str:
    total = max(0, int(round(value)))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _build_timeline_chunks(
    segments: list[dict[str, Any]],
    *,
    chunk_window_seconds: int,
    chunk_overlap_seconds: int,
) -> list[dict[str, Any]]:
    if not segments:
        return []
    if not any(isinstance(item.get("start"), (int, float)) for item in segments):
        return [
            {
                "start": None,
                "end": None,
                "lines": [str(item.get("text", "")).strip() for item in segments if str(item.get("text", "")).strip()],
            }
        ]

    window = max(60, int(chunk_window_seconds))
    overlap = max(0, min(window - 30, int(chunk_overlap_seconds)))
    max_end = max(float(item.get("end") or item.get("start") or 0.0) for item in segments)
    chunks: list[dict[str, Any]] = []
    start = 0.0
    while start <= max_end + 1:
        end = start + window
        lines: list[str] = []
        for item in segments:
            item_start = item.get("start")
            if not isinstance(item_start, (int, float)):
                continue
            if float(item_start) < start or float(item_start) >= end:
                continue
            label = _format_seconds_label(float(item_start))
            text = str(item.get("text", "")).strip()
            if text:
                lines.append(f"[{label}] {text}")
        if lines:
            chunks.append({"start": round(start, 3), "end": round(end, 3), "lines": lines})
        if end >= max_end:
            break
        start = max(0.0, end - overlap)
    return chunks


def _normalize_short_lines(values: Any, *, limit: int = 4) -> list[str]:
    if isinstance(values, str):
        candidates = re.split(r"[。\n；;]+", values)
    elif isinstance(values, list):
        candidates = [str(item) for item in values]
    else:
        candidates = []
    result: list[str] = []
    for raw in candidates:
        text = re.sub(r"\s+", " ", str(raw).strip()).strip("。；;")
        if not text or text in result:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _strip_time_prefix(value: Any) -> str:
    return re.sub(r"^\[[0-9:]+\]\s*", "", str(value or "").strip())


def _split_candidate_sentences(lines: list[str], *, limit: int = 24) -> list[str]:
    result: list[str] = []
    for raw in lines:
        cleaned = _strip_time_prefix(raw)
        parts = re.split(r"[。！？!?；;\n]+", cleaned)
        for part in parts:
            text = re.sub(r"\s+", " ", str(part).strip()).strip("，,：:；;。")
            if not text or len(text) < 8 or text in result:
                continue
            result.append(text)
            if len(result) >= limit:
                return result
    return result


def _merge_unique_lines(*groups: list[str], limit: int = 12) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for raw in group:
            text = re.sub(r"\s+", " ", str(raw).strip()).strip("。；;")
            if not text or text in merged:
                continue
            merged.append(text)
            if len(merged) >= limit:
                return merged
    return merged


_EXECUTIVE_FILLER_PATTERNS = [
    r"^(?:啊|嗯|呃|额|欸)\s*[，, ]*",
    r"^(?:那么|然后|所以|因此|其实|就是说|这个|那个|另外一方面|另外|总之)\s*[，, ]*",
    r"^(?:我觉得|我个人的话|我个人的思维啦|我说的话呢|我后面)\s*",
]

_EXECUTIVE_FILLER_TOKENS = [
    "对吧",
    "是吧",
    "的话",
    "这个",
    "那个",
    "然后",
    "就是说",
    "其实",
    "我觉得",
    "我说",
]

_EXECUTIVE_SIGNAL_TOKENS = [
    "估值",
    "现金",
    "现金流",
    "股息",
    "分红",
    "业绩",
    "成长",
    "风险",
    "回撤",
    "收益",
    "跑赢",
    "市场",
    "指数",
    "港股",
    "a股",
    "持有",
    "买入",
    "卖出",
    "减仓",
    "加仓",
    "调仓",
    "仓位",
    "计划",
    "切换",
    "估值",
    "面试",
    "offer",
    "评价表",
    "对齐",
    "定级",
    "流程",
    "方法",
    "步骤",
    "边界",
    "适合",
]


def _strip_executive_fillers(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip()).strip("。；;")
    if not text:
        return ""
    changed = True
    while changed:
        changed = False
        for pattern in _EXECUTIVE_FILLER_PATTERNS:
            updated = re.sub(pattern, "", text)
            if updated != text:
                text = updated.strip()
                changed = True
    text = re.sub(r"(对吧|是吧|好吗|你知道吧)(?=$|[，,。；; ])", "", text).strip("，,。；; ")
    return text


def _candidate_clauses(value: str) -> list[str]:
    text = re.sub(r"\s+", " ", str(value or "").strip()).strip("。；;")
    if not text:
        return []
    coarse_parts = [item for item in re.split(r"[。！？!?；;\n]+", text) if str(item).strip()]
    clauses: list[str] = []
    for coarse in coarse_parts:
        comma_parts = [item for item in re.split(r"[，,](?!\d)", coarse) if str(item).strip()]
        if not comma_parts:
            comma_parts = [coarse]
        for item in comma_parts:
            piece = re.sub(r"\s+", " ", str(item).strip()).strip("，,：:；;。 ")
            if not piece:
                continue
            subparts = [piece]
            if len(piece) > 64:
                subparts = [part for part in re.split(r"(?:但是|不过|因为|所以|因此|另外|同时|后续|如果|而且)", piece) if str(part).strip()]
            for part in subparts:
                cleaned = _strip_executive_fillers(str(part))
                cleaned = re.sub(r"\s+", " ", cleaned).strip("，,：:；;。 ")
                if cleaned and cleaned not in clauses:
                    clauses.append(cleaned)
    return clauses


def _executive_clause_score(value: str, signal_tokens: list[str] | None = None) -> tuple[int, int]:
    text = re.sub(r"\s+", " ", str(value or "").strip()).strip("。；;")
    if not text:
        return (-999, 0)
    tokens = signal_tokens or _EXECUTIVE_SIGNAL_TOKENS
    score = 0
    for token in tokens:
        if token and token in text:
            score += 2
    if re.search(r"\d|倍|%|股|年|月|日|天|季度|现金", text):
        score += 1
    length = len(text)
    if 10 <= length <= 56:
        score += 2
    elif 8 <= length <= 72:
        score += 1
    elif length > 96:
        score -= 2
    filler_hits = sum(text.count(token) for token in _EXECUTIVE_FILLER_TOKENS)
    score -= filler_hits
    if text.startswith(("但", "不过", "并", "同时", "也", "还")):
        score -= 1
    if text.endswith(("对吧", "是吧", "好吗")):
        score -= 1
    return score, -length


def _looks_like_noisy_asr_fragment(value: str) -> bool:
    text = re.sub(r"\s+", " ", str(value or "").strip()).strip("。；;")
    if not text:
        return False
    filler_hits = sum(text.count(token) for token in _EXECUTIVE_FILLER_TOKENS)
    if len(text) <= 10 and filler_hits:
        return True
    if filler_hits >= 3 and len(text) >= 18 and not any(token in text for token in _EXECUTIVE_SIGNAL_TOKENS):
        return True
    return False


def _normalize_executive_line(value: Any, *, max_len: int = 88) -> str:
    text = _strip_time_prefix(value)
    text = re.sub(r"\s+", " ", str(text).strip()).strip("。；;")
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return ""
    clauses = _candidate_clauses(text) or [_strip_executive_fillers(text)]
    ranked: list[tuple[tuple[int, int], str]] = []
    for clause in clauses:
        if not clause:
            continue
        ranked.append((_executive_clause_score(clause), clause))
    if ranked:
        ranked.sort(key=lambda item: item[0], reverse=True)
        text = ranked[0][1]
    else:
        text = _strip_executive_fillers(text)
    if _looks_like_noisy_asr_fragment(text):
        return ""
    if len(text) > max_len:
        clause_parts = [item.strip("，,：:；;。 ") for item in re.split(r"[，,]", text) if item.strip("，,：:；;。 ")]
        kept: list[str] = []
        for item in clause_parts:
            if len(item) < 4:
                continue
            kept.append(item)
            if len("，".join(kept)) >= max_len - 8 or len(kept) >= 2:
                break
        if kept:
            text = "，".join(kept)
    if len(text) > max_len:
        text = text[:max_len].rstrip("，,：:；; ") + "..."
    return text.strip()


def _strip_fact_label_prefix(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip()).strip("。；;")
    if not text:
        return ""
    return re.sub(r"^[^：:]{1,10}[：:]\s*", "", text).strip()


def _finance_signal_tokens(kind: str) -> list[str]:
    mapping = {
        "thesis": ["估值", "现金流", "股息", "成长", "业绩", "便宜", "低估", "高估", "价值", "分红", "稳定", "公允"],
        "position_change": ["持有", "继续持有", "买入", "卖出", "卖掉", "减仓", "加仓", "切换", "仓位", "调仓", "剩余", "计划", "观望"],
        "risk": ["风险", "回撤", "衰退", "不便宜", "高估", "波动", "下跌", "复杂", "太难", "回避", "验证", "空间有限"],
    }
    return mapping.get(kind, [])


def _normalize_finance_fact(value: Any, *, kind: str = "generic", max_len: int = 72, entity_name: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip()).strip("。；;")
    if not text:
        return ""
    clauses = _candidate_clauses(text)
    if entity_name and entity_name in text:
        named_windows = []
        for match in re.finditer(re.escape(entity_name), text):
            start = max(0, match.start() - 24)
            end = min(len(text), match.end() + 40)
            window = text[start:end]
            if window:
                named_windows.append(window)
        clauses = [*clauses, *named_windows]
    if not clauses:
        return ""
    tokens = _finance_signal_tokens(kind)
    relevant: list[str] = []
    for item in clauses:
        cleaned = _strip_executive_fillers(item)
        if not cleaned:
            continue
        if tokens and any(token in cleaned for token in tokens):
            relevant.append(cleaned)
    candidates = relevant or clauses
    ranked: list[tuple[tuple[int, int], str]] = []
    for item in candidates:
        cleaned = _strip_executive_fillers(item)
        if not cleaned:
            continue
        ranked.append((_executive_clause_score(cleaned, tokens or _EXECUTIVE_SIGNAL_TOKENS), cleaned))
    ranked.sort(key=lambda item: item[0], reverse=True)
    picked: list[str] = []
    for _, item in ranked:
        normalized = _normalize_executive_line(item, max_len=max_len)
        if not normalized or normalized in picked:
            continue
        picked.append(normalized)
        if len(picked) >= 2:
            break
    if kind == "position_change":
        picked = [item for item in picked if any(token in item for token in _finance_signal_tokens("position_change"))]
    if kind == "risk":
        picked = [item for item in picked if any(token in item for token in _finance_signal_tokens("risk"))]
    compact = "，".join(picked[:2]) if picked else ""
    if not compact:
        compact = _normalize_executive_line(candidates[0], max_len=max_len)
    return _normalize_executive_line(compact, max_len=max_len)


def _dedupe_finance_row_fields(row: dict[str, str]) -> dict[str, str]:
    thesis = row.get("thesis", "")
    position_change = row.get("position_change", "")
    risk = row.get("risk", "")
    if position_change and position_change == thesis:
        position_change = ""
    if risk and risk in {thesis, position_change}:
        risk = ""
    return {
        "name": row.get("name", ""),
        "sector": row.get("sector", ""),
        "thesis": thesis,
        "position_change": position_change,
        "risk": risk,
    }


def _looks_like_topic_list(value: str) -> bool:
    text = re.sub(r"\s+", " ", str(value or "").strip()).strip("。；;")
    if not text:
        return False
    separators = text.count("、") + text.count("与") + text.count("和")
    has_strong_signal = any(
        token in text for token in ["估值", "现金流", "持有", "继续", "卖出", "买入", "减仓", "加仓", "回撤", "不追高", "风险"]
    )
    return separators >= 2 and not has_strong_signal


def _video_direction_kind(evidence: EvidenceBundle) -> str:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    estimate = metadata.get("video_direction_estimate", {})
    if isinstance(estimate, dict):
        return str(estimate.get("kind", "")).strip()
    return ""


class EvidenceDrivenVideoSummarizer:
    def __init__(self, config: VideoSummaryConfig, client: AiHubMixGeminiSummarizer | None = None) -> None:
        self.config = config
        self.client = client or AiHubMixGeminiSummarizer(config)

    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
        segments = collect_timeline_segments(evidence)
        if not segments:
            speech_text = (evidence.transcript or evidence.text or "").strip()
            if not speech_text:
                raise RuntimeError("video summarizer requires timed speech evidence")
            segments = [{"source": "asr", "provider": "speech_fallback", "start": None, "end": None, "text": speech_text[:12000], "confidence": 0.6}]

        chunk_window_seconds, chunk_overlap_seconds = self._adaptive_chunk_settings(evidence, segments)

        chunks = _build_timeline_chunks(
            segments,
            chunk_window_seconds=chunk_window_seconds,
            chunk_overlap_seconds=chunk_overlap_seconds,
        )
        if not chunks:
            raise RuntimeError("video summarizer could not build timeline chunks")

        chunk_summaries: list[dict[str, Any]] = []
        chunk_summary_error = ""
        try:
            for index, chunk in enumerate(chunks, start=1):
                payload = {
                    "video_title": evidence.title or "",
                    "chunk_index": index,
                    "chunk_start": chunk.get("start"),
                    "chunk_end": chunk.get("end"),
                    "chunk_lines": chunk.get("lines", []),
                }
                raw = self.client.request_json_payload(CHUNK_SUMMARY_PROMPT, payload)
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise RuntimeError("invalid chunk summary payload")
                parsed.setdefault("start", chunk.get("start"))
                parsed.setdefault("end", chunk.get("end"))
                chunk_summaries.append(parsed)
        except Exception as exc:
            chunk_summary_error = str(exc)
            chunk_summaries = self._build_chunk_summaries_locally(chunks, evidence)

        evidence_basis = [
            "speech_tracks=" + ",".join(sorted({item.get("source", "") for item in segments if item.get("source")})),
            f"chunk_count={len(chunk_summaries)}",
            f"chunk_window_seconds={chunk_window_seconds}",
            f"chunk_overlap_seconds={chunk_overlap_seconds}",
        ]
        if chunk_summary_error:
            evidence_basis.append("chunk_summary_fallback=heuristic_lines")
            summary = self._fallback_summary_from_chunks(
                evidence=evidence,
                chunk_summaries=chunk_summaries,
                evidence_basis=evidence_basis,
                error=chunk_summary_error,
                allow_model_enrichment=False,
            )
            summary.outcome = summary.outcome if summary.outcome in {"summarized", "partial"} else "summarized"
            summary = self._postprocess_summary(summary, evidence, chunk_summaries)
            summary = self._finalize_executive_summary(summary, evidence, chunk_summaries)
            return _validate_and_normalize_summary(summary, evidence)
        final_payload = {
            "video_title": evidence.title or "",
            "source_url": evidence.source_url,
            "coverage": evidence.coverage,
            "evidence_basis": evidence_basis,
            "video_direction_kind": _video_direction_kind(evidence),
            "chunk_summaries": chunk_summaries,
        }
        try:
            raw = self.client.request_json_payload(FINAL_VIDEO_SUMMARY_PROMPT, final_payload)
            summary = SummaryResult.from_json(raw)
            summary.timeline_sections = self._merge_timeline_sections(summary.timeline_sections, chunk_summaries)
            if not summary.evidence_basis:
                summary.evidence_basis = evidence_basis
            summary.finance_matrix = self._normalize_finance_matrix(summary.finance_matrix)
            summary.finance_snapshot = self._normalize_finance_snapshot(summary.finance_snapshot)
            summary = self._maybe_attach_finance_structure(summary, evidence, chunk_summaries)
        except Exception as exc:
            summary = self._fallback_summary_from_chunks(
                evidence=evidence,
                chunk_summaries=chunk_summaries,
                evidence_basis=evidence_basis,
                error=str(exc),
                allow_model_enrichment=False,
            )
        summary.outcome = summary.outcome if summary.outcome in {"summarized", "partial"} else "summarized"
        summary = self._postprocess_summary(summary, evidence, chunk_summaries)
        summary = self._finalize_executive_summary(summary, evidence, chunk_summaries)
        return _validate_and_normalize_summary(summary, evidence)

    def _adaptive_chunk_settings(
        self,
        evidence: EvidenceBundle,
        segments: list[dict[str, Any]],
    ) -> tuple[int, int]:
        window = max(60, int(self.config.chunk_window_seconds))
        overlap = max(0, int(self.config.chunk_overlap_seconds))
        expected_outline_count = _expected_outline_count(evidence)
        duration_seconds = self._duration_seconds(evidence) or 0.0
        timed_segments = len([item for item in segments if isinstance(item.get("start"), (int, float))])

        if expected_outline_count >= 12:
            window = min(window, 90)
            overlap = min(max(10, overlap), 20)
        elif expected_outline_count >= 6:
            window = min(window, 120)
            overlap = min(max(10, overlap), 20)
        elif duration_seconds >= 900 or timed_segments >= 80:
            window = min(window, 120)
            overlap = min(max(10, overlap), 20)

        overlap = max(0, min(window - 30, overlap))
        return window, overlap

    def _postprocess_summary(
        self,
        summary: SummaryResult,
        evidence: EvidenceBundle,
        chunk_summaries: list[dict[str, Any]],
    ) -> SummaryResult:
        expected_outline_count = _expected_outline_count(evidence)
        observed_outline_count = _observed_outline_count(evidence)
        retained_outline_count = compute_retained_outline_count(
            evidence,
            summary,
            expected_outline_count=expected_outline_count,
        )
        if expected_outline_count > 0:
            summary.evidence_basis = [
                *summary.evidence_basis,
                f"expected_outline_count={expected_outline_count}",
                f"observed_outline_count={observed_outline_count}",
                f"retained_outline_count={retained_outline_count}",
            ]
        if expected_outline_count > 0 and retained_outline_count < expected_outline_count:
            summary.outcome = "partial"
            summary.coverage = "partial"
            gap_note = f"仅覆盖 {retained_outline_count}/{expected_outline_count} 项，不能当作完整枚举总结。"
            summary.uncertainties = [
                item
                for item in summary.uncertainties
                if not str(item).startswith("仅覆盖 ")
            ]
            if gap_note not in summary.uncertainties:
                summary.uncertainties = [*summary.uncertainties, gap_note]
            if len(summary.timeline_sections) < min(2, len(chunk_summaries)):
                summary.timeline_sections = self._build_timeline_sections_from_chunks(chunk_summaries[: min(4, len(chunk_summaries))])
        duration_seconds = self._duration_seconds(evidence)
        if duration_seconds and len(chunk_summaries) <= 1 and duration_seconds > float(self.config.chunk_window_seconds) * 1.25:
            summary.outcome = "partial"
            summary.coverage = "partial"
            chunk_note = f"当前仅生成 {len(chunk_summaries)} 个时间窗，总覆盖不足以代表整条长视频。"
            if chunk_note not in summary.uncertainties:
                summary.uncertainties = [*summary.uncertainties, chunk_note]
        summary.uncertainties = self._normalize_uncertainties(summary.uncertainties)
        return summary

    def _build_chunk_summaries_locally(
        self,
        chunks: list[dict[str, Any]],
        evidence: EvidenceBundle,
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            summaries.append(self._build_local_chunk_summary(chunk, evidence, index=index, total=total))
        return summaries

    def _build_local_chunk_summary(
        self,
        chunk: dict[str, Any],
        evidence: EvidenceBundle,
        *,
        index: int,
        total: int,
    ) -> dict[str, Any]:
        raw_lines = [str(item) for item in chunk.get("lines", []) if str(item).strip()]
        sentence_pool: list[str] = []
        for item in _split_candidate_sentences(raw_lines):
            normalized = _normalize_executive_line(item, max_len=88)
            if normalized and normalized not in sentence_pool:
                sentence_pool.append(normalized)
        finance_video = _video_direction_kind(evidence) == "finance_market"
        priority_tokens = [
            "估值",
            "现金流",
            "股息",
            "回撤",
            "收益",
            "跑赢",
            "赚钱",
            "风险",
            "计划",
            "持有",
            "买入",
            "卖出",
            "指数",
            "市场",
            "普通",
            "投资",
        ]

        def _score_sentence(text: str) -> tuple[int, int]:
            score = sum(1 for token in priority_tokens if token in text)
            if re.search(r"\d|倍|%|年|季", text):
                score += 1
            if 12 <= len(text) <= 60:
                score += 1
            if len(text) > 96:
                score -= 1
            return score, -len(text)

        ranked = sorted(sentence_pool, key=_score_sentence, reverse=True)
        cleaned_raw_lines = []
        for line in raw_lines:
            normalized = _normalize_executive_line(_strip_time_prefix(line), max_len=88)
            if normalized:
                cleaned_raw_lines.append(normalized)
        key_points = _merge_unique_lines(ranked, cleaned_raw_lines, limit=4)
        if not key_points:
            key_points = ["当前时间段已抓到语音内容，但只适合先看原视频复核。"]
        merged_text = " ".join([*_merge_unique_lines(key_points, limit=4), evidence.title or ""])
        heading = self._heuristic_chunk_heading(merged_text, finance_video=finance_video, index=index, total=total)
        summary = key_points[0]
        evidence_quotes = []
        for raw in raw_lines[:3]:
            text = re.sub(r"\s+", " ", raw.strip())
            if text and text not in evidence_quotes:
                evidence_quotes.append(text)
        return {
            "start": chunk.get("start"),
            "end": chunk.get("end"),
            "heading": heading,
            "summary": summary,
            "key_points": key_points,
            "evidence_quotes": evidence_quotes[:3],
            "uncertainties": [],
        }

    def _heuristic_chunk_heading(
        self,
        text: str,
        *,
        finance_video: bool,
        index: int,
        total: int,
    ) -> str:
        corpus = re.sub(r"\s+", " ", str(text or "").strip())
        lowered = corpus.lower()
        rules = _HEURISTIC_FINANCE_CHUNK_HEADINGS if finance_video else _HEURISTIC_GENERAL_CHUNK_HEADINGS
        best_label = ""
        best_score = 0
        for label, tokens in rules:
            score = sum(1 for token in tokens if token in corpus or token in lowered)
            if score > best_score:
                best_label = label
                best_score = score
        if best_label:
            return best_label
        if finance_video:
            if index <= max(1, total // 3):
                return "投资框架与核心观点"
            if index >= max(2, total - 1):
                return "风险控制与后续计划"
            return "估值与标的观察"
        if index == 1:
            return "开场与主题铺垫"
        if index == total:
            return "总结与收尾"
        return f"第{index}段内容"

    def _build_timeline_sections_from_chunks(
        self,
        chunk_summaries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "start": item.get("start"),
                "end": item.get("end"),
                "heading": item.get("heading", ""),
                "summary": item.get("summary", ""),
                "bullets": _normalize_short_lines(item.get("key_points", []), limit=4),
                "evidence": _normalize_short_lines(item.get("evidence_quotes", []), limit=3),
            }
            for item in chunk_summaries
        ]

    def _condense_timeline_sections_from_chunks(
        self,
        chunk_summaries: list[dict[str, Any]],
        evidence: EvidenceBundle,
    ) -> list[dict[str, Any]]:
        if not chunk_summaries:
            return []
        if len(chunk_summaries) <= 5:
            return self._build_timeline_sections_from_chunks(chunk_summaries)
        total = len(chunk_summaries)
        target_sections = 5 if total >= 9 else 4 if total >= 6 else 3
        target_sections = max(3, min(5, min(total, target_sections)))
        group_size = max(1, (total + target_sections - 1) // target_sections)
        finance_video = _video_direction_kind(evidence) == "finance_market"
        condensed: list[dict[str, Any]] = []
        for start_index in range(0, total, group_size):
            group = chunk_summaries[start_index : start_index + group_size]
            if not group:
                continue
            group_index = len(condensed) + 1
            summaries = _merge_unique_lines([str(item.get("summary", "")) for item in group], limit=4)
            bullets = _merge_unique_lines(
                [str(point) for item in group for point in item.get("key_points", []) if isinstance(item, dict)],
                limit=4,
            )
            evidence_quotes = _merge_unique_lines(
                [str(point) for item in group for point in item.get("evidence_quotes", []) if isinstance(item, dict)],
                limit=3,
            )
            merged_text = " ".join([*summaries, *bullets, evidence.title or ""])
            heading = self._heuristic_chunk_heading(
                merged_text,
                finance_video=finance_video,
                index=group_index,
                total=target_sections,
            )
            condensed.append(
                {
                    "start": group[0].get("start"),
                    "end": group[-1].get("end"),
                    "heading": heading,
                    "summary": summaries[0] if summaries else (bullets[0] if bullets else heading),
                    "bullets": bullets,
                    "evidence": evidence_quotes,
                }
            )
        return condensed[:5]

    def _merge_timeline_sections(
        self,
        timeline_sections: list[dict[str, Any]],
        chunk_summaries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not timeline_sections:
            return self._build_timeline_sections_from_chunks(chunk_summaries)
        merged: list[dict[str, Any]] = []
        for index, raw in enumerate(timeline_sections[:12]):
            if not isinstance(raw, dict):
                continue
            chunk = chunk_summaries[index] if index < len(chunk_summaries) and isinstance(chunk_summaries[index], dict) else {}
            bullets = _normalize_short_lines(raw.get("bullets", []), limit=4)
            if not bullets:
                bullets = _normalize_short_lines(raw.get("key_points", []), limit=4)
            if not bullets:
                bullets = _normalize_short_lines(chunk.get("key_points", []), limit=4)
            evidence = _normalize_short_lines(raw.get("evidence", []), limit=3)
            if not evidence:
                evidence = _normalize_short_lines(raw.get("evidence_quotes", []), limit=3)
            if not evidence:
                evidence = _normalize_short_lines(chunk.get("evidence_quotes", []), limit=3)
            merged.append(
                {
                    "start": raw.get("start", chunk.get("start")),
                    "end": raw.get("end", chunk.get("end")),
                    "heading": raw.get("heading", chunk.get("heading", "")),
                    "summary": raw.get("summary", chunk.get("summary", "")),
                    "bullets": bullets,
                    "evidence": evidence,
                }
            )
        return merged

    def _finalize_executive_summary(
        self,
        summary: SummaryResult,
        evidence: EvidenceBundle,
        chunk_summaries: list[dict[str, Any]],
    ) -> SummaryResult:
        executive_bullets = self._build_executive_bullets(summary, evidence, chunk_summaries)
        if executive_bullets:
            summary.bullets = executive_bullets
        if self._should_rewrite_conclusion(summary):
            summary.conclusion = self._build_executive_conclusion(summary, evidence)
        return summary

    def _should_rewrite_conclusion(self, summary: SummaryResult) -> bool:
        conclusion = re.sub(r"\s+", " ", str(summary.conclusion or "").strip())
        if not conclusion:
            return True
        blocked = [
            "已按时间段整理出视频主线",
            "视频按时间顺序讲了",
            "视频按时间顺序解释了",
            "视频讲了两个主要部分",
            "可先用于快速筛选",
        ]
        return any(token in conclusion for token in blocked)

    def _build_executive_conclusion(self, summary: SummaryResult, evidence: EvidenceBundle) -> str:
        bullets = [_strip_fact_label_prefix(item) for item in summary.bullets if _strip_fact_label_prefix(item)]
        primary = bullets[0] if bullets else ""
        secondary = bullets[1] if len(bullets) >= 2 else ""
        if _video_direction_kind(evidence) == "finance_market":
            if primary and secondary:
                return f"这条视频主要在讲{primary}，并把它落实到{secondary}。"
            if primary:
                return f"这条视频主要在讲{primary}。"
        if primary and secondary:
            return f"这条视频的核心信息是：{primary}；同时补充{secondary}。"
        if primary:
            return f"这条视频的核心信息是：{primary}。"
        title = re.sub(r"\s+", " ", str(evidence.title or "这条视频").strip())
        return f"这条视频主要围绕《{title}》展开。"

    def _build_executive_bullets(
        self,
        summary: SummaryResult,
        evidence: EvidenceBundle,
        chunk_summaries: list[dict[str, Any]],
    ) -> list[str]:
        if _video_direction_kind(evidence) == "finance_market":
            bullets = self._build_finance_executive_bullets(summary, evidence)
            if bullets:
                return bullets
        return self._build_general_executive_bullets(summary, evidence, chunk_summaries)

    def _build_finance_executive_bullets(
        self,
        summary: SummaryResult,
        evidence: EvidenceBundle,
    ) -> list[str]:
        lines: list[str] = []

        def _append(label: str, value: str) -> None:
            normalized = _normalize_executive_line(value)
            if not normalized:
                return
            line = f"{label}：{normalized}"
            if line not in lines:
                lines.append(line)

        for section in summary.timeline_sections:
            if not isinstance(section, dict):
                continue
            heading = re.sub(r"\s+", " ", str(section.get("heading", "")).strip())
            section_summary = re.sub(r"\s+", " ", str(section.get("summary", "")).strip())
            if not section_summary:
                continue
            if any(token in heading for token in ["投资", "框架", "哲学", "逻辑"]):
                _append("投资框架", section_summary)
                break

        snapshot = summary.finance_snapshot if isinstance(summary.finance_snapshot, dict) else {}
        market_view = snapshot.get("market_view", []) if isinstance(snapshot.get("market_view", []), list) else []
        performance_review = snapshot.get("performance_review", []) if isinstance(snapshot.get("performance_review", []), list) else []
        action_plan = snapshot.get("action_plan", []) if isinstance(snapshot.get("action_plan", []), list) else []
        if market_view:
            _append("市场判断", str(market_view[0]))
        if performance_review:
            _append("组合表现", str(performance_review[0]))

        row_phrases: list[str] = []
        risk_phrases: list[str] = []
        for row in summary.finance_matrix[:3]:
            if not isinstance(row, dict):
                continue
            name = re.sub(r"\s+", " ", str(row.get("name", "")).strip())
            thesis = _normalize_finance_fact(row.get("thesis", ""), kind="thesis")
            position_change = _normalize_finance_fact(row.get("position_change", ""), kind="position_change")
            risk = _normalize_finance_fact(row.get("risk", ""), kind="risk")
            pieces = [item for item in [thesis, position_change] if item]
            if name and pieces:
                row_phrases.append(f"{name}={ '，'.join(pieces[:2]) }")
            if name and risk:
                risk_phrases.append(f"{name}={risk}")
        if row_phrases:
            _append("标的取舍", "；".join(row_phrases[:2]))
        if action_plan:
            _append("后续计划", "；".join([_normalize_executive_line(item, max_len=48) for item in action_plan[:2] if _normalize_executive_line(item, max_len=48)]))
        if risk_phrases:
            _append("风险边界", "；".join(risk_phrases[:2]))

        return lines[:5]

    def _build_general_executive_bullets(
        self,
        summary: SummaryResult,
        evidence: EvidenceBundle,
        chunk_summaries: list[dict[str, Any]],
    ) -> list[str]:
        lines: list[str] = []

        def _append(value: str) -> None:
            normalized = _normalize_executive_line(value)
            if not normalized:
                return
            lowered = normalized.lower()
            if normalized.startswith(("视频链接:", "关键链接:", "GitHub地址:", "项目名称:", "技能名:", "技能ID:")):
                return
            if lowered.startswith(("http://", "https://")):
                return
            if normalized not in lines:
                lines.append(normalized)

        for raw in summary.bullets:
            _append(raw)

        for section in summary.timeline_sections:
            if not isinstance(section, dict):
                continue
            heading = re.sub(r"\s+", " ", str(section.get("heading", "")).strip())
            section_summary = re.sub(r"\s+", " ", str(section.get("summary", "")).strip())
            if heading and section_summary and heading not in section_summary:
                _append(f"{heading}：{section_summary}")
            else:
                _append(section_summary)
            for bullet in section.get("bullets", [])[:2] if isinstance(section.get("bullets", []), list) else []:
                _append(bullet)
            if len(lines) >= 5:
                break

        if len(lines) < 3:
            for chunk in chunk_summaries:
                if not isinstance(chunk, dict):
                    continue
                heading = re.sub(r"\s+", " ", str(chunk.get("heading", "")).strip())
                summary_line = re.sub(r"\s+", " ", str(chunk.get("summary", "")).strip())
                if heading and summary_line and heading not in summary_line:
                    _append(f"{heading}：{summary_line}")
                else:
                    _append(summary_line)
                if len(lines) >= 5:
                    break

        return lines[:5]

    def _normalize_finance_matrix(self, rows: list[dict[str, Any]] | Any) -> list[dict[str, str]]:
        if not isinstance(rows, list):
            return []
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw in rows[:12]:
            if not isinstance(raw, dict):
                continue
            name = re.sub(r"\s+", " ", str(raw.get("name", "")).strip())
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            row = _dedupe_finance_row_fields(
                {
                "name": name,
                "sector": _normalize_executive_line(raw.get("sector", ""), max_len=28),
                "thesis": _normalize_finance_fact(raw.get("thesis", ""), kind="thesis", entity_name=name),
                "position_change": _normalize_finance_fact(raw.get("position_change", ""), kind="position_change", entity_name=name),
                "risk": _normalize_finance_fact(raw.get("risk", ""), kind="risk", entity_name=name),
                }
            )
            if not any(row[field] for field in ["thesis", "position_change", "risk"]):
                continue
            normalized.append(row)
        return normalized

    def _normalize_finance_snapshot(self, snapshot: dict[str, Any] | Any) -> dict[str, list[str]]:
        if not isinstance(snapshot, dict):
            return {}
        normalized: dict[str, list[str]] = {}
        for key in ["market_view", "performance_review", "action_plan"]:
            lines = _normalize_short_lines(snapshot.get(key, []), limit=6)
            cleaned: list[str] = []
            for item in lines:
                normalized_line = _normalize_executive_line(item, max_len=72)
                if not normalized_line or normalized_line in cleaned:
                    continue
                cleaned.append(normalized_line)
                if len(cleaned) >= 4:
                    break
            if cleaned:
                normalized[key] = cleaned
        return normalized

    def _finance_snapshot_fallback(
        self,
        summary: SummaryResult,
    ) -> dict[str, list[str]]:
        snapshot = self._normalize_finance_snapshot(summary.finance_snapshot)
        if snapshot:
            return snapshot
        fallback: dict[str, list[str]] = {}
        market_tokens = ["市场", "指数", "恒指", "恒生", "港股", "a股", "赚钱效应", "行情", "国家队", "保卫战"]
        review_tokens = ["收益", "回撤", "跑赢", "组合", "目标", "业绩", "回血"]
        plan_tokens = ["后续", "计划", "调仓", "持有", "买入", "卖出", "不追高", "回避", "防御"]
        for section in summary.timeline_sections:
            if not isinstance(section, dict):
                continue
            heading = re.sub(r"\s+", " ", str(section.get("heading", "")).strip())
            section_summary = re.sub(r"\s+", " ", str(section.get("summary", "")).strip())
            bullets = _normalize_short_lines(section.get("bullets", []), limit=3)
            lines = [item for item in [section_summary, *bullets] if item]
            if not lines:
                continue
            matched_market = [line for line in lines if any(token.lower() in line.lower() for token in market_tokens)]
            matched_review = [line for line in lines if any(token.lower() in line.lower() for token in review_tokens)]
            matched_plan = [line for line in lines if any(token.lower() in line.lower() for token in plan_tokens)]
            if any(token in heading for token in ["市场", "指数", "赚钱效应", "观点"]) or matched_market:
                fallback.setdefault("market_view", []).extend((matched_market or lines[:1])[:2])
            if any(token in heading for token in ["业绩", "收益", "回顾", "复盘"]) or matched_review:
                fallback.setdefault("performance_review", []).extend((matched_review or lines[:1])[:2])
            if any(token in heading for token in ["计划", "后续", "操作", "持仓", "风险"]) or matched_plan:
                fallback.setdefault("action_plan", []).extend((matched_plan or lines[:1])[:2])
        return {key: _normalize_short_lines(values, limit=4) for key, values in fallback.items() if values}

    def _maybe_attach_finance_structure(
        self,
        summary: SummaryResult,
        evidence: EvidenceBundle,
        chunk_summaries: list[dict[str, Any]],
        *,
        allow_model_enrichment: bool = True,
    ) -> SummaryResult:
        if _video_direction_kind(evidence) != "finance_market":
            summary.finance_matrix = []
            summary.finance_snapshot = {}
            return summary
        if not summary.finance_matrix and allow_model_enrichment:
            payload = {
                "video_title": evidence.title or "",
                "source_url": evidence.source_url,
                "coverage": summary.coverage,
                "timeline_sections": summary.timeline_sections[:6],
                "chunk_summaries": chunk_summaries[:8],
                "transcript_excerpt": (evidence.transcript or evidence.text or "")[:9000],
            }
            try:
                raw = self.client.request_json_payload(FINANCE_VIDEO_STRUCT_PROMPT, payload)
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    summary.finance_matrix = self._normalize_finance_matrix(parsed.get("finance_matrix", []))
                    summary.finance_snapshot = self._normalize_finance_snapshot(parsed.get("finance_snapshot", {}))
            except Exception:
                pass
        if not summary.finance_matrix:
            summary.finance_matrix = self._heuristic_finance_matrix(evidence, summary, chunk_summaries)
        if not summary.finance_snapshot:
            summary.finance_snapshot = self._finance_snapshot_fallback(summary)
        return summary

    def _heuristic_finance_matrix(
        self,
        evidence: EvidenceBundle,
        summary: SummaryResult,
        chunk_summaries: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
        context_lines = _merge_unique_lines(
            [str(item) for item in metadata.get("transcript_timeline_lines", [])[:80]] if isinstance(metadata.get("transcript_timeline_lines"), list) else [],
            [str(item) for item in metadata.get("timeline_highlights", [])[:40]] if isinstance(metadata.get("timeline_highlights"), list) else [],
            [str(item) for item in metadata.get("keyframe_ocr_lines", [])[:24]] if isinstance(metadata.get("keyframe_ocr_lines"), list) else [],
            [str(item.get("summary", "")) for item in chunk_summaries if isinstance(item, dict)],
            [str(point) for item in chunk_summaries if isinstance(item, dict) for point in item.get("key_points", [])],
            [str(point) for row in summary.timeline_sections for point in row.get("bullets", []) if isinstance(row, dict)],
            _split_candidate_sentences([(evidence.transcript or evidence.text or "")], limit=36),
            limit=120,
        )
        corpus = "\n".join(context_lines)
        if not corpus.strip():
            return []

        def _normalize_entity_name(value: str) -> str:
            text = re.sub(r"\s+", " ", value.strip())
            for canonical, info in _FINANCE_ENTITY_ALIASES.items():
                aliases = [canonical, *[str(item) for item in info.get("aliases", [])]]
                if text in aliases:
                    return canonical
            if text == "腾讯":
                return "腾讯控股"
            if text == "骗仔癀":
                return "片仔癀"
            return text

        candidate_names: list[str] = []
        for canonical, info in _FINANCE_ENTITY_ALIASES.items():
            aliases = [canonical, *[str(item) for item in info.get("aliases", [])]]
            if any(alias and alias in corpus for alias in aliases):
                if canonical not in candidate_names:
                    candidate_names.append(canonical)
        for match in _FINANCE_ENTITY_PATTERN.finditer(corpus):
            name = _normalize_entity_name(match.group(1))
            if name and name not in _FINANCE_ENTITY_STOPWORDS and name not in candidate_names:
                candidate_names.append(name)

        def _infer_sector(lines: list[str], name: str) -> str:
            preset = _FINANCE_ENTITY_ALIASES.get(name, {}).get("sector", "")
            if preset:
                return str(preset)
            joined = "\n".join(lines)
            mapping = [
                ("消费/餐饮", ["餐饮", "门店", "翻台"]),
                ("消费/饮料", ["饮料", "食品", "啤酒", "乳制品"]),
                ("医药/中药", ["医药", "中药", "药"]),
                ("能源/石油", ["石油", "油价", "能源"]),
                ("航运", ["航运", "运价", "海控"]),
                ("互联网/平台", ["互联网", "广告", "流量", "游戏"]),
                ("金融", ["银行", "保险", "证券", "金融"]),
            ]
            for label, tokens in mapping:
                if any(token in joined for token in tokens):
                    return label
            return ""

        def _pick_line(lines: list[str], tokens: list[str], *, used: set[str] | None = None) -> str:
            used_lines = used or set()
            for raw in lines:
                candidates = _split_candidate_sentences([raw], limit=4) or [_strip_time_prefix(raw)]
                for text in candidates:
                    if text and not _looks_like_topic_list(text) and text not in used_lines and any(token in text for token in tokens):
                        return text
            for raw in lines:
                candidates = _split_candidate_sentences([raw], limit=4) or [_strip_time_prefix(raw)]
                for text in candidates:
                    if text and not _looks_like_topic_list(text) and any(token in text for token in tokens):
                        return text
            return ""

        rows: list[dict[str, str]] = []
        for name in candidate_names[:6]:
            aliases = [name, *[str(item) for item in _FINANCE_ENTITY_ALIASES.get(name, {}).get("aliases", [])]]
            related = [line for line in context_lines if any(alias and alias in line for alias in aliases)]
            if not related:
                continue
            sector = _infer_sector(related, name)
            used_lines: set[str] = set()
            thesis_raw = _pick_line(related, ["估值", "现金流", "股息", "业绩", "成长", "便宜", "低估", "高估", "分红", "价值"], used=used_lines)
            if thesis_raw:
                used_lines.add(thesis_raw)
            position_raw = _pick_line(related, ["持有", "继续持有", "买入", "卖出", "卖掉", "减仓", "加仓", "切换", "仓位", "调仓", "剩余", "计划"], used=used_lines)
            if position_raw:
                used_lines.add(position_raw)
            risk_raw = _pick_line(related, ["风险", "回撤", "衰退", "不便宜", "高估", "波动", "下跌", "复杂", "太难", "回避", "空间有限"], used=used_lines)
            thesis = _normalize_finance_fact(thesis_raw, kind="thesis", entity_name=name)
            position_change = _normalize_finance_fact(position_raw, kind="position_change", entity_name=name)
            risk = _normalize_finance_fact(risk_raw, kind="risk", entity_name=name)
            if not any([sector, thesis, position_change, risk]):
                continue
            rows.append(
                _dedupe_finance_row_fields(
                    {
                    "name": name,
                    "sector": sector,
                    "thesis": thesis,
                    "position_change": position_change,
                    "risk": risk,
                    }
                )
            )
        return self._normalize_finance_matrix(rows)

    def _fallback_summary_from_chunks(
        self,
        *,
        evidence: EvidenceBundle,
        chunk_summaries: list[dict[str, Any]],
        evidence_basis: list[str],
        error: str,
        allow_model_enrichment: bool = False,
    ) -> SummaryResult:
        timeline_sections = self._condense_timeline_sections_from_chunks(chunk_summaries, evidence)
        bullets: list[str] = []
        for section in timeline_sections:
            heading = re.sub(r"\s+", " ", str(section.get("heading", "")).strip())
            section_summary = re.sub(r"\s+", " ", str(section.get("summary", "")).strip())
            if heading and section_summary:
                bullets.append(f"{heading}：{section_summary}")
            elif section_summary:
                bullets.append(section_summary)
            elif heading:
                bullets.append(heading)
            if len(bullets) >= 5:
                break
        if not bullets:
            bullets = ["当前已拿到整片语音证据，但最终汇总模型未返回稳定结果。"]
        conclusion = "已按时间段整理出视频主线，可先用于快速筛选；细节仍建议回看原视频复核。"
        summary = SummaryResult(
            title=str(evidence.title or "长视频总结"),
            primary_topic="视频",
            secondary_topics=[],
            entities=[],
            conclusion=conclusion,
            bullets=bullets,
            evidence_quotes=_normalize_short_lines(
                [item for chunk in chunk_summaries[:3] for item in chunk.get("evidence_quotes", []) if isinstance(chunk, dict)],
                limit=4,
            ),
            coverage="partial",
            confidence="medium",
            note_tags=["video_chunk_fallback"],
            follow_up_actions=[],
            timeliness="medium",
            effectiveness="medium",
            recommendation_level="recommended",
            reader_judgment="当前已经能看出视频主线，适合先看结构，再决定是否回看细节。",
            outcome="partial",
            evidence_basis=[*evidence_basis, "final_summary_fallback=chunk_summaries"],
            timeline_sections=timeline_sections,
            uncertainties=[f"最终汇总模型失败：{error}"] if error else [],
            refusal_reason="",
        )
        summary = self._maybe_attach_finance_structure(
            summary,
            evidence,
            chunk_summaries,
            allow_model_enrichment=allow_model_enrichment,
        )
        if not summary.finance_snapshot:
            summary.finance_snapshot = self._finance_snapshot_fallback(summary)
        return summary

    def _ordered_outline_points(self, evidence: EvidenceBundle) -> list[str]:
        candidates = []
        for field in [evidence.text, evidence.transcript or ""]:
            points = _extract_enumerated_points_from_text(field)
            if points:
                candidates = points
                break
        if candidates:
            return [f"{index + 1}. {point}" for index, point in enumerate(candidates)]
        point_pattern = re.compile(
            r"(问题[一二三四五六七八九十\d]{1,3}[^。；\n]{0,40}|第[一二三四五六七八九十\d]{1,3}名[^。；\n]{0,40}|(?:\d{1,2}|[一二三四五六七八九十])(?:[、.）)]\s*[^。；\n]{2,40}))"
        )
        seen: list[str] = []
        for match in point_pattern.finditer((evidence.text or "") + "\n" + (evidence.transcript or "")):
            item = re.sub(r"\s+", " ", match.group(1).strip()).strip("。；;")
            if item and item not in seen:
                seen.append(item)
        return [f"{index + 1}. {point}" for index, point in enumerate(seen)]

    def _enumeration_probe_conclusion(self, summary: SummaryResult, expected_outline_count: int, retained_outline_count: int) -> str:
        base = re.sub(r"\s+", " ", str(summary.conclusion or "").strip()).strip("。")
        if not base:
            base = "当前已拿到视频的部分关键条目"
        return f"{base}；当前已覆盖 {retained_outline_count}/{expected_outline_count} 项，可先用于快速判断，但不能当作完整版本。"

    def _normalize_uncertainties(self, values: list[str]) -> list[str]:
        if values and len(values) >= 4 and all(len(str(item).strip()) == 1 for item in values):
            merged = "".join(str(item).strip() for item in values).strip()
            return [merged] if merged else []
        result: list[str] = []
        for raw in values:
            text = " ".join(str(raw).split()).strip()
            if text and text not in result:
                result.append(text)
        return result[:8]

    def _duration_seconds(self, evidence: EvidenceBundle) -> float | None:
        metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
        for field in ["video_duration_seconds", "bilibili_duration_seconds"]:
            try:
                value = float(metadata.get(field))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None
