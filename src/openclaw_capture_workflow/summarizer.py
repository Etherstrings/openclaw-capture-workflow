"""OpenAI-compatible conservative summarizer client."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from .config import SummarizerConfig
from .content_profile import infer_content_profile, iter_required_signal_entries
from .models import EvidenceBundle, SummaryResult
from .video_story_blocks import (
    get_qualified_video_story_blocks,
    get_story_block_bullets,
    get_story_block_outline_points,
    get_viewer_feedback,
)


PROMPT = """Role: Direct Analyst

Profile:
Your job is to summarize fragmented evidence into a clear, factual brief.
You are not a decorative narrator and not a persona. You reconstruct noisy evidence into a direct, useful answer.

Core mission:
- reconstruct fragmented evidence into a coherent understanding
- decide what matters and what does not
- explain the current situation in plain, competent language
- surface what should be paid attention to next

Capabilities:
- Semantic reconstruction: the evidence may be sliced, partially OCR'd, noisy, or structurally broken. Rebuild the likely logic chain.
- Multi-mode summarization: produce concise factual output depending on source type.

Important:
- Only use facts present in the evidence. Do not invent unstated facts.
- If certainty is limited, say so explicitly.
- Keep the result concise, human, and useful.

Return strict JSON with these keys:
title, primary_topic, secondary_topics, entities, conclusion, bullets,
evidence_quotes, coverage, confidence, note_tags, follow_up_actions,
timeliness, effectiveness, recommendation_level, reader_judgment

Heuristics:
1. Scan and denoise:
   - filter UI noise, ads, duplicated fragments, and broken formatting
   - identify core entities, products, links, actions, and claims
2. Reconstruct:
   - infer which fragments belong together
   - repair truncated sentences when the intended meaning is obvious from adjacent evidence
   - prefer the most plausible complete reading, but do not fabricate unsupported details
3. Analyze:
   - state what is happening now
   - explain why it matters or why it likely happened
   - extract what the user should do next
   - assess risk if ignored

Rules:
- Write in Chinese.
- Write like a strong direct answer to the user: calm, concrete, useful.
- Prioritize this order: what this is, why it matters, what action follows, and whether it deserves attention now.
- Do not waste words on filler like "这是一个很好的问题".
- Do not use persona language, stage directions, or roleplay framing.
- Avoid stiff report language and empty packaging.
- Never use second-person wording such as "你/你可以/对你有用" in the structured summary fields.
- coverage must be one of: full, partial
- confidence must be one of: high, medium, low
- timeliness must be one of: high, medium, low
- effectiveness must be one of: high, medium, low
- recommendation_level must be one of: must_read, recommended, optional, skip
- reader_judgment must be one sentence stating the practical judgment based on available evidence.
- title must be short and semantic (no site UI prefix like "GitHub -" / "小红书 -").
- conclusion must be one sentence and directly state the main finding.
- bullets should be 3 to 6 concise points, each point only one fact.
- follow_up_actions should be 2 to 6 executable checklist items when content is setup/install/tutorial.
- evidence_quotes should be short phrases copied from the evidence.
- If the evidence looks like a tutorial (contains steps such as "一、" / "二、" or "步骤"), bullets must reflect the actual steps.
- If the evidence is setup/install tutorial, ensure output contains: prerequisites, key commands, validation checkpoint.
- If evidence is incomplete, set coverage=partial and say so in conclusion.
- If the evidence is about a Skill/tool recommendation, prioritize: skill name, source link, install/use method, required model or token cost notes.
- Do not miss GitHub links, command lines, hashtags, or explicit "Skill" names if present in evidence.
- If a skill slug/id appears (for example `tech-earnings-deepdive`), include it as a key point.
- For GitHub links, prioritize README facts: what it is, how to install/use, and exact repo URL.
- The first 1-2 bullets should be the highest-value facts, not generic scene-setting.
- If `metadata.content_profile.kind=skill_recommendation`, must include: skill name, skill id, repo/source link, install method, use method.
- If `metadata.content_profile.kind=installation_tutorial`, must include: prerequisites, key commands, validation step.
- If `metadata.content_profile.kind=project_overview`, must include: project name, repo/doc link, core purpose, run/use boundary when present.
- For long videos or long articles, do not produce a book report. Surface the real takeaway first.
- Avoid boilerplate/meta statements like "已提取核心事实" or "帮助你快速理解".
- Avoid vague phrases like "内容完整/覆盖全面/适用于开发者和爱好者" unless the evidence explicitly states them.
- Do not repeat the same fact in different bullets with slight wording changes.
- If the evidence clearly enumerates multiple points, preserve the main sequence when it is reliable; if the details are noisy, prefer a few clean paragraph-like bullets over a forced full list.
- If `video_outline.outline_detected=true`, use it as a hint, not a hard requirement.
- If `video_story_blocks` is present, use it only when it clearly improves readability; otherwise prefer simple timeline paragraphs.
- If `video_story_blocks` contains `workflow`, `risk`, or `viewer_feedback`, only use those blocks when the evidence is explicit.
- Do not copy long raw ASR fragments into bullets; rewrite them as concise topic blocks.
- If `viewer_feedback` is empty, do not invent audience reaction.
"""

PROMPT_VERSION = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()[:16]


class SummaryEngine(Protocol):
    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
        ...


class OpenAICompatibleSummarizer:
    def __init__(self, config: SummarizerConfig) -> None:
        self.config = config

    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
        video_context = _build_video_prompt_context(evidence)
        payload = {
            "model": self.config.model,
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
            raise RuntimeError(f"summarizer request failed: {exc}") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"unexpected summarizer response: {body}") from exc
        summary = SummaryResult.from_json(content)
        return _validate_and_normalize_summary(summary, evidence)


def _normalize_list(values: list[str], limit: int = 10) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in result:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _normalize_coverage(value: str, fallback: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"full", "partial"}:
        return normalized
    return fallback if fallback in {"full", "partial"} else "partial"


def _normalize_confidence(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"high", "medium", "low"}:
        return normalized
    return "medium"


def _normalize_level(value: str, fallback: str = "medium") -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"high", "medium", "low"}:
        return normalized
    return fallback


def _normalize_recommendation_level(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"must_read", "recommended", "optional", "skip"}:
        return normalized
    return "optional"


def _sanitize_display_url(value: str) -> str:
    text = (value or "").strip()
    if not text.startswith(("http://", "https://")):
        return text
    try:
        parsed = urlsplit(text)
    except ValueError:
        return text
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
    items = parse_qsl(parsed.query, keep_blank_values=False)
    filtered = [(k, v) for k, v in items if k not in ignored_keys and not k.startswith("utm_")]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", urlencode(filtered, doseq=True), ""))


def _refine_title(value: str, evidence: EvidenceBundle) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text:
        text = re.sub(r"\s+", " ", (evidence.title or "").strip())
    if not text:
        return "未命名内容"
    text = re.sub(r"^(GitHub|github)\s*-\s*", "", text).strip()
    text = re.sub(r"[_\-\s]*哔哩哔哩[_\-\s]*bilibili$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s*[-|]\s*哔哩哔哩.*$", "", text, flags=re.IGNORECASE).strip()
    duplicate_match = re.match(r"^(.+?)\s*-\s*\1$", text)
    if duplicate_match:
        text = duplicate_match.group(1).strip()
    if len(text) > 80:
        text = text[:80].rstrip()
    return text or "未命名内容"


def _fallback_bullets(evidence: EvidenceBundle, limit: int = 5) -> list[str]:
    bullets: list[str] = []
    for line in [line.strip() for line in evidence.text.splitlines() if line.strip()]:
        line = re.sub(r"\s+", " ", line)
        if len(line) < 8 or len(line) > 120:
            continue
        if line in bullets:
            continue
        bullets.append(line)
        if len(bullets) >= limit:
            break
    return bullets


_ENUM_PATTERNS = [
    re.compile(r"^\s*(\d{1,2})[.)、]\s*(.+)$"),
    re.compile(r"^\s*第\s*(\d{1,2})\s*点[:：]?\s*(.+)$"),
    re.compile(r"^\s*(十一|十二|十|[一二三四五六七八九])[、.）)]\s*(.+)$"),
]

_CN_ENUM_MAP = {
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
}


def _chinese_enum_to_int(value: str) -> int | None:
    token = (value or "").strip()
    if token in _CN_ENUM_MAP:
        return _CN_ENUM_MAP[token]
    if token == "十一":
        return 11
    if token == "十二":
        return 12
    return None


def _extract_section_outline_points(evidence: EvidenceBundle, *, max_points: int = 12) -> list[str]:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    structured = metadata.get("structured_document", {}) if isinstance(metadata.get("structured_document"), dict) else {}
    sections = structured.get("sections", []) if isinstance(structured.get("sections"), list) else []
    points: list[str] = []
    generic_tokens = ["播放", "控制", "权限", "推荐", "用户互动", "基本信息", "标签", "页面", "概述", "结论", "证据"]
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = re.sub(r"\s+", " ", str(section.get("heading", "")).strip())
        content = re.sub(r"\s+", " ", str(section.get("content", "")).strip())
        if heading and heading not in points and len(heading) >= 4 and not any(token in heading for token in generic_tokens):
            points.append(heading)
        elif content and len(content) >= 8:
            snippet = content[:48].rstrip("，。；; ")
            if snippet and snippet not in points:
                points.append(snippet)
        if len(points) >= max_points:
            break
    return points if len(points) >= 2 else []


def _extract_step_outline_points(evidence: EvidenceBundle, *, max_points: int = 12) -> list[str]:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    step_items = metadata.get("step_items", []) if isinstance(metadata.get("step_items"), list) else []
    points: list[str] = []
    for item in step_items:
        if not isinstance(item, dict):
            continue
        title = re.sub(r"\s+", " ", str(item.get("title", "")).strip())
        detail = re.sub(r"\s+", " ", str(item.get("detail", "")).strip())
        text = title or detail
        if text and text not in points:
            points.append(text)
        if len(points) >= max_points:
            break
    return points if len(points) >= 2 else []


def _extract_timestamp_outline_points(text: str, *, max_points: int = 12) -> list[str]:
    points: list[str] = []
    for raw_line in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line.strip())
        match = re.match(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+)$", line)
        if not match:
            continue
        content = match.group(2).strip()
        if len(content) < 4:
            continue
        if content not in points:
            points.append(content)
        if len(points) >= max_points:
            break
    return points if len(points) >= 2 else []


def _extract_sequence_outline_points(text: str) -> list[str]:
    compact = re.sub(r"\s+", " ", text or "")
    patterns = [
        re.compile(r"(?:首先|先)([^。；\n]{2,60})"),
        re.compile(r"(?:其次|再|然后)([^。；\n]{2,60})"),
        re.compile(r"(?:最后|最终)([^。；\n]{2,60})"),
    ]
    points: list[str] = []
    for pattern in patterns:
        match = pattern.search(compact)
        if not match:
            continue
        content = match.group(1).strip(" ，,：:;；")
        if content and content not in points:
            points.append(content)
    return points if len(points) >= 2 else []


def _extract_enumerated_points_from_text(text: str, *, min_points: int = 3, max_points: int = 12) -> list[str]:
    found: list[tuple[int, str]] = []
    for raw_line in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line.strip())
        if len(line) < 4:
            continue
        number = None
        content = ""
        for pattern in _ENUM_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            token = match.group(1)
            content = match.group(2).strip()
            if pattern is _ENUM_PATTERNS[2]:
                number = _chinese_enum_to_int(token)
            else:
                try:
                    number = int(token)
                except ValueError:
                    number = None
            break
        if number is None or not content:
            continue
        if number < 1 or number > max_points:
            continue
        if len(content) < 2:
            continue
        found.append((number, content))

    if not found:
        return []
    dedup_by_number: dict[int, str] = {}
    for number, content in found:
        dedup_by_number.setdefault(number, content)
    ordered = sorted(dedup_by_number.items(), key=lambda item: item[0])
    numbers = [number for number, _ in ordered]
    if len(numbers) < min_points:
        return []
    expected = list(range(numbers[0], numbers[0] + len(numbers)))
    if numbers != expected:
        return []
    return [content for _, content in ordered]


def _extract_enumerated_points(evidence: EvidenceBundle, bullets: list[str]) -> list[str]:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    candidates = []
    for field in [
        evidence.text,
        evidence.transcript or "",
        str(metadata.get("video_page_snapshot_text", "")),
    ]:
        if field:
            candidates.append(str(field))
    for candidate in candidates:
        points = _extract_enumerated_points_from_text(candidate)
        if points:
            return points
    return []


def _extract_explicit_video_outline(evidence: EvidenceBundle, bullets: list[str]) -> list[str]:
    if evidence.source_kind != "video_url":
        return []
    for extractor in [
        lambda: _extract_section_outline_points(evidence),
        lambda: _extract_step_outline_points(evidence),
        lambda: _extract_enumerated_points(evidence, bullets),
    ]:
        points = extractor()
        if points:
            return points[:12]
    return []


def _extract_video_outline(evidence: EvidenceBundle, bullets: list[str]) -> list[str]:
    explicit_points = _extract_explicit_video_outline(evidence, bullets)
    if explicit_points:
        return explicit_points[:12]
    story_points = get_story_block_outline_points(evidence, include_feedback=False)
    if story_points:
        return story_points[:12]
    if evidence.source_kind != "video_url":
        return []
    sequence_points = _extract_sequence_outline_points(evidence.transcript or evidence.text)
    return sequence_points[:12]


def _build_video_story_payload(evidence: EvidenceBundle) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for block in get_qualified_video_story_blocks(evidence):
        label = str(block.get("label", "")).strip()
        summary = re.sub(r"\s+", " ", str(block.get("summary", "")).strip())
        evidence_items = block.get("evidence", [])
        if not label or not summary or not isinstance(evidence_items, list):
            continue
        payload.append(
            {
                "label": label,
                "summary": summary,
                "evidence": [re.sub(r"\s+", " ", str(item).strip()) for item in evidence_items if str(item).strip()][:3],
            }
        )
    return payload[:6]


def _build_video_outline_payload(evidence: EvidenceBundle) -> dict[str, object]:
    explicit_points = _extract_explicit_video_outline(evidence, [])
    story_points = get_story_block_outline_points(evidence, include_feedback=False)
    points = explicit_points or story_points or _extract_sequence_outline_points(evidence.transcript or evidence.text)
    return {
        "outline_detected": bool(points),
        "outline_points": points,
        "hierarchy_depth": 1 if points else 0,
        "outline_source": "explicit" if explicit_points else "story_blocks" if story_points else "sequence" if points else "none",
        "evidence_tracks": (
            evidence.metadata.get("evidence_sources", [])
            if isinstance(evidence.metadata, dict) and isinstance(evidence.metadata.get("evidence_sources"), list)
            else []
        ),
    }


def _build_video_prompt_context(evidence: EvidenceBundle) -> dict[str, object]:
    return {
        "video_outline": _build_video_outline_payload(evidence),
        "video_story_blocks": _build_video_story_payload(evidence),
        "viewer_feedback": get_viewer_feedback(evidence),
    }


def _normalize_bullet_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value).strip())
    text = text.strip("。；;")
    # Keep full URLs for traceability; truncating URLs breaks downstream recall checks.
    max_len = 220 if ("http://" in text or "https://" in text) else 120
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "..."
    return text


def _normalize_finance_cell_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value).strip()).strip("。；;")
    if not text:
        return ""
    parts = [item.strip("，,：:；;。 ") for item in re.split(r"[。；;\n]+", text) if item.strip("，,：:；;。 ")]
    if parts:
        text = parts[0]
    if len(text) > 96:
        text = text[:96].rstrip("，,：:；; ") + "..."
    return text


def _is_generic_bullet(value: str) -> bool:
    lowered = value.lower()
    generic_tokens = [
        "内容完整",
        "覆盖全面",
        "适用于开发者",
        "信息完整",
        "提供了完整",
        "该证据",
        "该链接",
        "该项目",
    ]
    return any(token in lowered for token in generic_tokens)


def _content_profile_kind(evidence: EvidenceBundle) -> str:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    profile = metadata.get("content_profile", {}) if isinstance(metadata.get("content_profile"), dict) else {}
    if not profile:
        profile = infer_content_profile(evidence.source_kind, evidence.source_url, evidence.text, metadata)
    kind = str(profile.get("kind", "")).strip()
    return kind or "general_capture"


def _is_docs_overview_page(evidence: EvidenceBundle) -> bool:
    source_url = (evidence.source_url or "").strip().lower().rstrip("/")
    if not source_url.startswith("https://docs."):
        return False
    if source_url.count("/") > 2:
        return False
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
    commands = signals.get("commands", []) if isinstance(signals.get("commands"), list) else []
    prerequisites = signals.get("prerequisites", []) if isinstance(signals.get("prerequisites"), list) else []
    validations = signals.get("validation_actions", []) if isinstance(signals.get("validation_actions"), list) else []
    step_items = metadata.get("step_items", []) if isinstance(metadata.get("step_items"), list) else []
    return not bool(commands or prerequisites or validations) and bool(step_items or signals.get("supported_platforms"))


def _labeled_bullet_parts(value: str) -> tuple[str, str]:
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


def _looks_like_resource_bullet(value: str) -> bool:
    label, body = _labeled_bullet_parts(value)
    lowered = body.lower()
    return label in {"GitHub地址", "视频链接", "关键链接", "仓库地址", "文档链接", "来源链接"} or lowered.startswith(
        ("http://", "https://")
    )


def _looks_like_boundary_bullet(value: str) -> bool:
    label, body = _labeled_bullet_parts(value)
    text = f"{label} {body}".lower()
    if label in {"适用边界", "常见错误", "验证边界", "风险", "边界", "限制"}:
        return True
    return any(token in text for token in ["边界", "限制", "不支持", "仅支持", "注意事项", "风险", "报错", "错误", "失败", "缺口"])


def _normalize_signal_values(
    values: list[str],
    *,
    limit: int = 2,
    sanitize_url: bool = False,
    label: str = "",
) -> list[str]:
    normalized: list[str] = []
    for raw in values:
        text = str(raw).strip()
        if not text:
            continue
        if sanitize_url:
            text = _sanitize_display_url(text)
        if label:
            for prefix in (f"{label}:", f"{label}："):
                if text.startswith(prefix):
                    text = text[len(prefix) :].strip()
                    break
        text = re.sub(r"\s+", " ", text).strip()
        if not text or text in normalized:
            continue
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def _signal_priority_bullets(evidence: EvidenceBundle, limit: int = 4) -> list[str]:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    signals = metadata.get("signals", {})
    if not isinstance(signals, dict):
        return []
    bullets: list[str] = []
    profile_kind = _content_profile_kind(evidence)
    projects = [str(item) for item in signals.get("projects", []) if str(item).strip()]
    links = [_sanitize_display_url(str(item)) for item in signals.get("links", []) if str(item).strip()]
    skills = [str(item) for item in signals.get("skills", []) if str(item).strip()]
    skill_ids = [str(item) for item in signals.get("skill_ids", []) if str(item).strip()]
    commands = [str(item) for item in signals.get("commands", []) if str(item).strip()]
    prerequisites = [str(item) for item in signals.get("prerequisites", []) if str(item).strip()]
    validations = [str(item) for item in signals.get("validation_actions", []) if str(item).strip()]
    use_cases = [str(item) for item in signals.get("use_cases", []) if str(item).strip()]
    purposes = [str(item) for item in signals.get("purposes", []) if str(item).strip()]
    boundaries = [str(item) for item in signals.get("boundaries", []) if str(item).strip()]
    common_errors = [str(item) for item in signals.get("common_errors", []) if str(item).strip()]
    supported_platforms = [str(item) for item in signals.get("supported_platforms", []) if str(item).strip()]
    step_items = metadata.get("step_items", []) if isinstance(metadata.get("step_items"), list) else []
    step_titles = [
        re.sub(r"\s+", " ", str(item.get("title", "")).strip())
        for item in step_items
        if isinstance(item, dict) and str(item.get("title", "")).strip()
    ]

    # Prefer canonical repo/doc links before raw install artifacts.
    candidate_links = [
        link
        for link in links
        if "/raw/" not in link.lower() and not link.lower().endswith(".skill")
    ]
    raw_links = [link for link in links if link not in candidate_links]
    ordered_links = _normalize_list(candidate_links + raw_links, limit=6)

    github_links = [link for link in ordered_links if "github.com" in link.lower()]
    video_links = [
        link
        for link in ordered_links
        if any(domain in link.lower() for domain in ["youtube.com", "youtu.be", "bilibili.com", "vimeo.com"])
    ]
    other_links = [link for link in ordered_links if link not in github_links and link not in video_links]

    evidence_lower = (evidence.text or "").lower()
    if projects and github_links:
        repo_link_set = {link.lower() for link in github_links}
        ranked_projects = []
        for project in projects:
            canonical = f"https://github.com/{project}".lower()
            explicit_repo_mention = bool(re.search(re.escape(canonical) + r"(?!/raw/)", evidence_lower))
            score = 0
            if explicit_repo_mention:
                score += 2
            if canonical in repo_link_set:
                score += 1
            if any(link.lower().startswith(canonical + "/raw/") for link in links) and not explicit_repo_mention:
                score -= 1
            ranked_projects.append((score, project))
        projects = [item[1] for item in sorted(ranked_projects, key=lambda pair: pair[0], reverse=True)]

    def append_line(label: str, values: list[str], *, cap: int = 2, sanitize_url: bool = False) -> None:
        normalized_values = _normalize_signal_values(values, limit=cap, sanitize_url=sanitize_url, label=label)
        if not normalized_values:
            return
        bullet = _normalize_bullet_text(f"{label}: " + " | ".join(normalized_values))
        if bullet and bullet not in bullets:
            bullets.append(bullet)

    if profile_kind == "installation_tutorial":
        append_line("前置条件", prerequisites)
        if commands:
            has_install_command = any(token in cmd.lower() for cmd in commands for token in ["install", "/install-skill"])
            append_line("安装方法" if has_install_command else "关键命令", commands)
        append_line("验证动作", validations)
        append_line("平台支持", supported_platforms, cap=4)
        append_line("适用边界", boundaries)
        append_line("常见错误", common_errors)
        append_line("项目名称", projects, cap=1)
        append_line("GitHub地址", github_links, sanitize_url=True)
        append_line("关键链接", other_links, sanitize_url=True)
    elif profile_kind == "skill_recommendation":
        append_line("技能名", skills)
        append_line("技能ID", skill_ids, cap=3)
        if commands:
            has_install_command = any(token in cmd.lower() for cmd in commands for token in ["install", "/install-skill"])
            append_line("安装方法" if has_install_command else "关键命令", commands)
        append_line("使用方式", use_cases or purposes)
        append_line("项目名称", projects, cap=1)
        append_line("GitHub地址", github_links, sanitize_url=True)
        append_line("平台支持", supported_platforms, cap=4)
        append_line("适用边界", boundaries)
        append_line("验证动作", validations)
        append_line("常见错误", common_errors)
        append_line("关键链接", other_links, sanitize_url=True)
    elif profile_kind == "project_overview":
        append_line("项目名称", projects, cap=1)
        append_line("GitHub地址", github_links, sanitize_url=True)
        append_line("核心用途", purposes or use_cases)
        if commands:
            has_install_command = any(token in cmd.lower() for cmd in commands for token in ["install", "/install-skill"])
            append_line("安装方法" if has_install_command else "关键命令", commands)
        append_line("平台支持", supported_platforms, cap=4)
        append_line("适用边界", boundaries)
        append_line("验证动作", validations)
        append_line("关键链接", other_links, sanitize_url=True)
    else:
        append_line("项目名称", projects, cap=1)
        append_line("技能名", skills)
        append_line("技能ID", skill_ids, cap=3)
        append_line("前置条件", prerequisites)
        if commands:
            has_install_command = any(token in cmd.lower() for cmd in commands for token in ["install", "/install-skill"])
            append_line("安装方法" if has_install_command else "关键命令", commands)
        append_line("验证动作", validations)
        append_line("使用方式", use_cases)
        append_line("核心用途", purposes)
        append_line("平台支持", supported_platforms, cap=4)
        append_line("适用边界", boundaries)
        append_line("常见错误", common_errors)
        if step_titles:
            append_line("流程要点", step_titles, cap=3)
        append_line("GitHub地址", github_links, sanitize_url=True)
        if video_links and evidence.source_kind == "video_url":
            append_line("视频链接", video_links, sanitize_url=True)
        append_line("关键链接", other_links, sanitize_url=True)
    if _is_docs_overview_page(evidence) and step_titles:
        append_line("流程要点", step_titles, cap=3)
        if not boundaries:
            append_line("适用边界", ["当前页更像文档首页概览，没有给出完整命令、验证和失败处理。"])

    normalized = _normalize_list([_normalize_bullet_text(item) for item in bullets], limit=max(limit + 2, 6))
    if any(item.startswith("平台支持:") for item in normalized):
        normalized = [
            item
            for item in normalized
            if item not in {"支持多平台", "支持多个平台", "支持多平台服务"} and not item.startswith("支持多平台")
        ]
    if boundaries or common_errors:
        has_boundary = any(_looks_like_boundary_bullet(item) for item in normalized)
        if not has_boundary:
            boundary_values = boundaries or common_errors
            boundary_label = "适用边界" if boundaries else "常见错误"
            boundary_line = _normalize_bullet_text(
                f"{boundary_label}: " + " | ".join(_normalize_signal_values(boundary_values, limit=2, label=boundary_label))
            )
            if boundary_line:
                if len(normalized) >= max(limit, 1):
                    replace_idx = next((idx for idx in range(len(normalized) - 1, -1, -1) if _looks_like_resource_bullet(normalized[idx])), len(normalized) - 1)
                    normalized[replace_idx] = boundary_line
                else:
                    normalized.append(boundary_line)
    if profile_kind == "general_capture":
        fact_count = sum(1 for item in normalized if not _looks_like_resource_bullet(item))
        if fact_count < 3 and other_links and not any(item.startswith("关键链接:") for item in normalized):
            normalized.append("关键链接: " + " | ".join(_normalize_signal_values(other_links, limit=2, sanitize_url=True)))
    return _normalize_list(_dedupe_fact_categories(normalized), limit=limit)


def _dedupe_fact_categories(items: list[str]) -> list[str]:
    category_labels = {
        "项目名称",
        "GitHub地址",
        "视频链接",
        "关键链接",
        "文档链接",
        "来源链接",
        "技能名",
        "技能ID",
        "关键命令",
        "安装方法",
        "前置条件",
        "验证动作",
        "使用方式",
        "核心用途",
        "流程要点",
        "平台支持",
        "适用边界",
        "常见错误",
        "命令",
        "链接",
        "项目",
    }
    seen_labels: set[str] = set()
    result: list[str] = []
    for item in items:
        if ":" in item:
            label = item.split(":", 1)[0].strip()
        elif "：" in item:
            label = item.split("：", 1)[0].strip()
        else:
            label = ""
        if label in category_labels:
            if label in seen_labels:
                continue
            seen_labels.add(label)
        result.append(item)
    return result


def _is_incomplete_video(evidence: EvidenceBundle) -> bool:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    video_gate = metadata.get("video_gate_reasons") if isinstance(metadata, dict) else None
    return evidence.source_kind == "video_url" and isinstance(video_gate, list) and bool(video_gate)


def _is_video_probe_mode(evidence: EvidenceBundle) -> bool:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    profile = str(metadata.get("video_extraction_profile", "")).strip().lower()
    return evidence.source_kind == "video_url" and "probe" in profile


def _strip_fact_label(value: str) -> str:
    text = re.sub(r"^\[[0-9:]+\]\s*", "", str(value).strip())
    for sep in (":", "："):
        if sep not in text:
            continue
        label, rest = text.split(sep, 1)
        if len(label.strip()) <= 8:
            text = rest.strip()
        break
    return text


def _clean_video_fact_text(value: str, evidence: EvidenceBundle) -> str:
    text = _normalize_bullet_text(_strip_fact_label(value))
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return ""
    title = _refine_title(evidence.title, evidence)
    if text == title:
        return ""
    if text.startswith(("标题 ", "标题:", "标题：", "标签:", "标签：", "UP主:", "UP主：", "播放量:", "点赞量:")):
        return ""
    for prefix in [
        "视频中介绍了",
        "视频介绍了",
        "视频详细解析了",
        "视频详细讲解了",
        "视频讲解了",
        "视频主要讲",
        "视频讲了",
        "视频指出",
        "视频强调了",
        "强调了",
        "作者强调了",
    ]:
        if text.startswith(prefix):
            text = text[len(prefix) :].lstrip("，,：: ")
            break
    if _is_video_probe_mode(evidence) and any(token in text for token in ["视频时长", "时长约", "时长为"]):
        return ""
    if any(token in text for token in ["播放量", "点赞量"]):
        return ""
    return text


def _extract_video_fact_points(bullets: list[str], evidence: EvidenceBundle, limit: int = 3) -> list[str]:
    facts: list[str] = []
    for raw in bullets:
        normalized = _normalize_bullet_text(raw)
        if not normalized:
            continue
        if normalized.startswith(("视频链接:", "关键链接:", "GitHub地址:", "项目名称:", "技能名:", "技能ID:")):
            continue
        cleaned = _clean_video_fact_text(normalized, evidence)
        if not cleaned:
            continue
        if len(cleaned) < 8 or len(cleaned) > 70:
            continue
        if cleaned not in facts:
            facts.append(cleaned)
        if len(facts) >= limit:
            break
    return facts[:limit]


def _video_evidence_fallback_bullets(evidence: EvidenceBundle, limit: int = 4) -> list[str]:
    bullets: list[str] = []
    source_url = _sanitize_display_url(evidence.source_url or "")
    if source_url:
        label = "视频链接" if any(domain in source_url.lower() for domain in ["youtube.com", "youtu.be", "bilibili.com"]) else "关键链接"
        bullets.append(f"{label}: {source_url}")
    title = _refine_title(evidence.title, evidence)
    if title and title != "未命名内容":
        bullets.append(f"主题: {title}")
    for line in [item.strip() for item in (evidence.text or "").splitlines() if item.strip()]:
        normalized = _normalize_bullet_text(line)
        if not normalized or normalized == title:
            continue
        if normalized.startswith("[") and normalized.endswith("]"):
            continue
        if normalized.startswith(("http://", "https://")):
            continue
        if any(token in normalized for token in ["相关推荐", "点赞", "收藏", "评论", "关注", "播放"]):
            continue
        lowered = normalized.lower()
        has_signal = any(
            token in lowered
            for token in ["攻略", "教程", "安装", "部署", "开源", "github", "skill", "薪资流水", "背调", "offer", "选牌", "路线"]
        )
        if len(normalized) < 18 and not has_signal:
            continue
        if any(token in normalized for token in ["有用的", "心计", "笑死", "傻傻"]):
            continue
        if normalized not in bullets:
            bullets.append(normalized)
        if len(bullets) >= limit:
            break
    return bullets[:limit]


def _refine_incomplete_video_bullets(bullets: list[str], evidence: EvidenceBundle) -> list[str]:
    evidence_corpus = "\n".join(
        [
            _sanitize_display_url(evidence.source_url or ""),
            _refine_title(evidence.title, evidence),
            evidence.text or "",
        ]
    ).lower()
    title_terms = [term.lower() for term in re.split(r"[\s/|_-]+", _refine_title(evidence.title, evidence)) if len(term) >= 2]
    filtered: list[str] = []
    for item in bullets:
        value = item
        if ":" in value:
            value = value.split(":", 1)[1].strip()
        elif "：" in value:
            value = value.split("：", 1)[1].strip()
        normalized = _normalize_requirement_token(value)
        if not normalized:
            continue
        if any(token in normalized for token in ["我p的图", "心计", "有用的", "烂苹果", "换一位", "笑死", "傻傻"]):
            continue
        if any(token in normalized for token in ["全面", "完整", "进阶", "深入", "系统讲解"]) and normalized not in evidence_corpus:
            continue
        has_domain_signal = any(
            token in normalized
            for token in [
                "攻略",
                "教程",
                "安装",
                "部署",
                "开源",
                "github",
                "skill",
                "薪资流水",
                "背调",
                "offer",
                "选牌",
                "路线",
                "大公司",
                "入职",
            ]
        )
        if not has_domain_signal and title_terms and not any(term in normalized for term in title_terms):
            continue
        if normalized.startswith(("http://", "https://")) or normalized in evidence_corpus:
            filtered.append(item)
            continue
        if len(normalized) >= 8 and normalized[: min(16, len(normalized))] in evidence_corpus:
            filtered.append(item)
    refined = _normalize_list(_dedupe_fact_categories(filtered), limit=5)
    if len(refined) < 3:
        refined = _normalize_list(_dedupe_fact_categories(refined + _video_evidence_fallback_bullets(evidence, limit=5)), limit=5)
    return refined[:5]


def _looks_like_raw_video_bullet(value: str, evidence: EvidenceBundle) -> bool:
    text = _normalize_bullet_text(value)
    if not text:
        return False
    if re.fullmatch(r"要点\d+", text):
        return True
    if len(text) >= 90:
        return True
    compact = re.sub(r"\s+", "", text.lower())
    transcript_corpus = re.sub(r"\s+", "", (evidence.transcript or evidence.text or "").lower())
    if compact and len(compact) >= 32 and compact in transcript_corpus and not re.search(r"[，。；：:,]", text):
        return True
    return False


def _refine_bullets(summary_bullets: list[str], evidence: EvidenceBundle) -> list[str]:
    if evidence.source_kind == "video_url":
        evidence_corpus = "\n".join(
            [
                _sanitize_display_url(evidence.source_url or ""),
                _refine_title(evidence.title, evidence),
                evidence.text or "",
                evidence.transcript or "",
            ]
        ).lower()
        story_bullets = get_story_block_bullets(evidence, include_feedback=True, limit=6)
        blocked_video_templates = [
            "视频把关键流程拆成了输入、配置和运行几个环节，重点在把方案真正跑起来。",
            "系统会结合行情、业绩和多种数据源来做判断，而不只是给一句结论。",
            "视频明确提醒这更像技术展示和参考，不建议盲目跟单或直接照搬投资决策。",
            "评论区主要围绕实盘体验、可靠性和使用边界展开讨论。",
        ]
        video_candidates: list[str] = []
        weak_video_candidate_count = 0
        for raw in summary_bullets:
            bullet = _normalize_bullet_text(raw)
            if not bullet or _is_generic_bullet(bullet):
                continue
            if _looks_like_raw_video_bullet(bullet, evidence):
                weak_video_candidate_count += 1
                continue
            if any(token in bullet for token in ["素材未包含", "素材不包含", "当前素材未包含"]):
                weak_video_candidate_count += 1
                continue
            if any(template in bullet for template in blocked_video_templates):
                normalized = bullet.lower()
                if normalized not in evidence_corpus:
                    weak_video_candidate_count += 1
                    continue
            cleaned_bullet = _clean_video_fact_text(bullet, evidence)
            if not cleaned_bullet and not bullet.startswith(("视频链接:", "关键链接:", "GitHub地址:", "项目名称:", "技能名:", "技能ID:")):
                weak_video_candidate_count += 1
                continue
            if len(cleaned_bullet or bullet) < 10 and not bullet.startswith(("视频链接:", "关键链接:", "GitHub地址:", "项目名称:", "技能名:", "技能ID:")):
                weak_video_candidate_count += 1
            video_candidates.append(bullet if bullet.startswith(("视频链接:", "关键链接:", "GitHub地址:", "项目名称:", "技能名:", "技能ID:")) else cleaned_bullet)
        video_candidates = _normalize_list(_dedupe_fact_categories(video_candidates), limit=6)
        if story_bullets:
            candidate_corpus = "\n".join(video_candidates).lower()
            story_hits = sum(1 for item in story_bullets if _normalize_bullet_text(item).lower() in candidate_corpus)
            if story_hits == 0:
                return [f"{idx + 1}. {point}" for idx, point in enumerate(story_bullets)]
        if story_bullets and (len(video_candidates) < 3 or weak_video_candidate_count > 0):
            return [f"{idx + 1}. {point}" for idx, point in enumerate(story_bullets)]
        explicit_outline = _extract_explicit_video_outline(evidence, summary_bullets)
        if explicit_outline and len(explicit_outline) > len(video_candidates):
            return [f"{idx + 1}. {point}" for idx, point in enumerate(explicit_outline)]
        if len(video_candidates) >= 3:
            return video_candidates[:6]
        if explicit_outline:
            return [f"{idx + 1}. {point}" for idx, point in enumerate(explicit_outline)]
        if story_bullets:
            return [f"{idx + 1}. {point}" for idx, point in enumerate(story_bullets)]
        outline_points = _extract_video_outline(evidence, summary_bullets)
        if outline_points:
            return [f"{idx + 1}. {point}" for idx, point in enumerate(outline_points)]

    candidates: list[str] = []
    for raw in summary_bullets:
        bullet = _normalize_bullet_text(raw)
        if not bullet:
            continue
        if _is_generic_bullet(bullet):
            continue
        candidates.append(bullet)

    # Always prioritize signal-derived facts first to keep output precise.
    prioritized = _signal_priority_bullets(evidence, limit=4)
    merged = _normalize_list(_dedupe_fact_categories(prioritized + candidates), limit=6)
    merged_corpus = "\n".join(merged).lower()
    evidence_lower = (evidence.text or "").lower()
    appended_focus: list[str] = []
    for term in ["container runtime", "kubelet"]:
        if term in evidence_lower and term not in merged_corpus:
            focus_line = f"关键术语: {term}"
            merged.append(focus_line)
            appended_focus.append(focus_line)
            merged_corpus += "\n" + term
            break
    if ("安装" in evidence_lower or "/install-skill" in evidence_lower) and "安装方法" not in merged_corpus:
        install_line = "安装方法: 执行证据中的 /install-skill 命令并按步骤验证。"
        merged.append(install_line)
        appended_focus.append(install_line)
        merged_corpus += "\n安装方法"
    if len(merged) < 3:
        fallback = [_normalize_bullet_text(item) for item in _fallback_bullets(evidence, limit=7)]
        merged = _normalize_list(_dedupe_fact_categories(merged + fallback), limit=6)
    if _is_docs_overview_page(evidence):
        flow_bullet = next((item for item in merged if item.startswith("流程要点:")), "")
        if flow_bullet:
            _, flow_text = _labeled_bullet_parts(flow_bullet)
            flow_parts = {part.strip() for part in flow_text.split("|") if part.strip()}
            merged = [item for item in merged if item == flow_bullet or item not in flow_parts]
    if prioritized and len(merged) > 6:
        base = merged[:6]
        if appended_focus:
            for focus_line in appended_focus:
                if focus_line in base:
                    continue
                for idx in range(len(base) - 1, -1, -1):
                    if not base[idx].startswith(
                        ("项目名称:", "GitHub地址:", "视频链接:", "关键链接:", "技能名:", "技能ID:", "安装方法:")
                    ):
                        base[idx] = focus_line
                        break
                else:
                    base[-1] = focus_line
                break
        merged = base
    if evidence.source_kind == "video_url":
        filtered_video_lines: list[str] = []
        for item in merged:
            if _is_video_probe_mode(evidence):
                cleaned = _clean_video_fact_text(item, evidence)
                if not cleaned and any(token in item for token in ["视频时长", "时长约", "时长为"]):
                    continue
            filtered_video_lines.append(item)
        if filtered_video_lines:
            merged = filtered_video_lines[:6]
    if _is_incomplete_video(evidence):
        merged = _refine_incomplete_video_bullets(merged, evidence)
    return merged


def _is_tutorial_like(evidence: EvidenceBundle) -> bool:
    text = (evidence.text or "").lower()
    source = (evidence.source_url or "").lower()
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    content_profile = metadata.get("content_profile", {}) if isinstance(metadata.get("content_profile"), dict) else {}
    if not content_profile:
        content_profile = infer_content_profile(evidence.source_kind, evidence.source_url, evidence.text, metadata)
    if content_profile.get("kind") in {"skill_recommendation", "installation_tutorial"}:
        return True
    tutorial_tokens = [
        "安装",
        "教程",
        "步骤",
        "setup",
        "install",
        "/install-skill",
        "配置",
        "命令：",
        "how to",
    ]
    if any(token in text for token in tutorial_tokens):
        return True
    if "cloud.tencent.com/developer/article" in source:
        return True
    if metadata.get("step_items") or metadata.get("steps"):
        return True
    signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
    commands = signals.get("commands", []) if isinstance(signals.get("commands"), list) else []
    if commands:
        return True
    return False


def _extract_install_actions_from_evidence(evidence: EvidenceBundle, limit: int = 6) -> list[str]:
    actions: list[str] = []
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
    commands = signals.get("commands", []) if isinstance(signals.get("commands"), list) else []
    validations = signals.get("validation_actions", []) if isinstance(signals.get("validation_actions"), list) else []
    for cmd in commands:
        text = _normalize_bullet_text(str(cmd))
        if not text:
            continue
        line = f"执行命令：{text}"
        if line not in actions:
            actions.append(line)
        if len(actions) >= limit:
            return actions
    for item in validations:
        text = _normalize_bullet_text(str(item))
        if not text:
            continue
        line = text if text.startswith(("验证", "检查", "确认")) else f"验证动作：{text}"
        if line not in actions:
            actions.append(line)
        if len(actions) >= limit:
            return actions

    steps = metadata.get("steps", []) if isinstance(metadata.get("steps"), list) else []
    for step in steps:
        text = _normalize_bullet_text(str(step))
        if not text:
            continue
        if text not in actions:
            actions.append(text)
        if len(actions) >= limit:
            return actions

    for line in [line.strip() for line in (evidence.text or "").splitlines() if line.strip()]:
        normalized = _normalize_bullet_text(line)
        lowered = normalized.lower()
        if not normalized:
            continue
        if len(normalized) > 90:
            continue
        if normalized.startswith(("http://", "https://")):
            continue
        if any(token in lowered for token in ["安装", "执行", "运行", "配置", "验证", "启动", "/install-skill", ".skill"]):
            if normalized not in actions:
                actions.append(normalized)
        if len(actions) >= limit:
            return actions
    return actions


def _refine_follow_up_actions(actions: list[str], evidence: EvidenceBundle, bullets: list[str]) -> list[str]:
    normalized = _normalize_list([_normalize_bullet_text(item) for item in actions if _normalize_bullet_text(item)], limit=8)
    if evidence.source_kind == "video_url":
        metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
        profile = metadata.get("content_profile", {}) if isinstance(metadata.get("content_profile"), dict) else {}
        profile_kind = str(profile.get("kind", "")).strip()
        signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
        if _is_incomplete_video(evidence):
            guided = [
                "补抓字幕或语音轨后再复核结论",
                "回到原视频确认完整上下文",
            ]
            merged = _normalize_list(guided + normalized, limit=4)
            return merged[:4]
        title = _refine_title(evidence.title, evidence)
        filtered: list[str] = []
        for item in normalized:
            if item.startswith("标题:") or item.startswith("标题："):
                continue
            if re.match(r"^\[[0-9:]+\]", item):
                continue
            if item.startswith(("观看视频", "回看视频", "打开原视频", "点开视频")):
                continue
            if "获取详细步骤" in item:
                continue
            if "评论区" in item:
                continue
            if title != "未命名内容" and title and title in item:
                continue
            filtered.append(item)
        if len(filtered) >= 2:
            return filtered[:4]
        supports_tutorial_actions = profile_kind in {"installation_tutorial", "skill_recommendation", "project_overview"}
        supports_tutorial_actions = supports_tutorial_actions or bool(
            isinstance(signals, dict)
            and (
                signals.get("commands")
                or signals.get("projects")
                or signals.get("skill_ids")
                or signals.get("validation_actions")
            )
        )
        if not supports_tutorial_actions:
            return filtered[:4]
        video_facts = _extract_video_fact_points(bullets, evidence, limit=3)
        derived: list[str] = []
        joined = "\n".join(video_facts + [str(metadata.get("user_guidance", ""))]).lower()
        if any(token in joined for token in ["项目", "技术点", "学项目", "有没有帮助"]):
            derived.append("先确认项目是干什么的、关键技术点是什么，再决定是否继续投入")
        if any(token in joined for token in ["下载", "运行", "跑起来", "部署"]):
            derived.append("挑一个示例项目先下载并跑起来")
        if any(token in joined for token in ["看不懂", "不会运行", "不会使用", "读代码"]):
            derived.append("把不会运行和看不懂代码的卡点记下来，再逐个验证")
        if any(token in joined for token in ["trae", "chain"]) and any(token in joined for token in ["部署", "本地"]):
            derived.append("用TRAE或chain把项目先部署到本地试跑")
        if any(token in joined for token in ["全英文", "英文"]):
            derived.append("先确认自己能否接受全英文界面和英文信息源")
        merged = _normalize_list(filtered + derived, limit=4)
        return merged[:4]
    if _is_incomplete_video(evidence):
        guided = [
            "补抓字幕或语音轨后再复核结论",
            "回到原视频确认完整上下文",
        ]
        merged = _normalize_list(guided + normalized, limit=4)
        return merged[:4]
    if _is_docs_overview_page(evidence):
        return ["如果要真正开始安装，继续进入详细安装或配对子页查看具体命令和验证步骤。"]
    tutorial_like = _is_tutorial_like(evidence)
    if tutorial_like:
        install_actions = _extract_install_actions_from_evidence(evidence, limit=8)
        merged = _normalize_list(install_actions + normalized, limit=8)
        if len(merged) < 2:
            merged = _normalize_list(merged + bullets[:3], limit=5)
        return merged[:6]
    return normalized[:4]


def _refine_conclusion(conclusion: str, evidence: EvidenceBundle, bullets: list[str]) -> str:
    clean = re.sub(r"\s+", " ", (conclusion or "").strip())
    if not clean:
        return clean
    if evidence.source_kind == "video_url":
        is_generic_video_conclusion = (
            _is_generic_bullet(clean)
            or clean in {"已提取核心事实。", "已提取核心事实", "提取完成。", "提取完成", "完成。", "完成"}
            or len(clean) < 12
        )
        if is_generic_video_conclusion:
            facts = _extract_video_fact_points(bullets, evidence, limit=2)
            if len(facts) >= 2:
                return f"视频的核心意思是：{facts[0]}；同时补充{facts[1]}。"
            if facts:
                return f"视频的核心意思是：{facts[0]}。"
            title = _refine_title(evidence.title, evidence)
            if title and title != "未命名内容":
                return f"视频主要围绕《{title}》展开。"
    if _is_docs_overview_page(evidence):
        metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
        step_items = metadata.get("step_items", []) if isinstance(metadata.get("step_items"), list) else []
        step_titles = [
            re.sub(r"\s+", " ", str(item.get("title", "")).strip())
            for item in step_items
            if isinstance(item, dict) and str(item.get("title", "")).strip()
        ]
        signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
        supported_platforms = signals.get("supported_platforms", []) if isinstance(signals.get("supported_platforms"), list) else []
        platform_text = "、".join([str(item) for item in supported_platforms[:4] if str(item).strip()])
        title = _refine_title(evidence.title or "", evidence)
        if len(step_titles) >= 2:
            return (
                f"当前拿到的是《{title}》的概览页，不是完整安装文档；"
                f"能确认的大致流程是先{step_titles[0]}，再{step_titles[1]}，但具体命令、验证和失败处理还没给出。"
            )
        if platform_text:
            return (
                f"当前拿到的是《{title}》的概览页，不是完整安装文档；"
                f"目前只能确认它支持{platform_text}，以及大致安装/配对方向。"
            )
    signals = {}
    if isinstance(evidence.metadata, dict) and isinstance(evidence.metadata.get("signals"), dict):
        signals = evidence.metadata["signals"]
    projects = signals.get("projects", []) if isinstance(signals, dict) else []
    skills = signals.get("skills", []) if isinstance(signals, dict) else []
    skill_ids = signals.get("skill_ids", []) if isinstance(signals, dict) else []
    if evidence.source_kind != "video_url" and (_is_generic_bullet(clean) or clean in {"已提取核心事实。", "已提取核心事实"} or len(clean) < 18):
        profile_kind = _content_profile_kind(evidence)
        if _is_docs_overview_page(evidence):
            metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
            step_items = metadata.get("step_items", []) if isinstance(metadata.get("step_items"), list) else []
            step_titles = [
                re.sub(r"\s+", " ", str(item.get("title", "")).strip())
                for item in step_items
                if isinstance(item, dict) and str(item.get("title", "")).strip()
            ]
            signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
            supported_platforms = signals.get("supported_platforms", []) if isinstance(signals.get("supported_platforms"), list) else []
            platform_text = "、".join([str(item) for item in supported_platforms[:4] if str(item).strip()])
            if len(step_titles) >= 2:
                return (
                    f"当前拿到的是《{_refine_title(evidence.title or '', evidence)}》的概览页，不是完整安装文档；"
                    f"能确认的大致流程是先{step_titles[0]}，再{step_titles[1]}，但具体命令、验证和失败处理还没给出。"
                )
            if platform_text:
                return (
                    f"当前拿到的是《{_refine_title(evidence.title or '', evidence)}》的概览页，不是完整安装文档；"
                    f"目前只能确认它支持{platform_text}，以及大致安装/配对方向。"
                )
        subject = ""
        if projects:
            subject = f"项目 {projects[0]}"
        elif skills:
            subject = f"技能 {skills[0]}"
        elif skill_ids:
            subject = f"技能ID {skill_ids[0]}"
        else:
            title = _refine_title(evidence.title or "", evidence)
            if title and title != "未命名内容":
                subject = f"《{title}》"
        fact_values: list[str] = []
        boundary_value = ""
        for item in bullets:
            if _looks_like_resource_bullet(item):
                continue
            label, body = _labeled_bullet_parts(item)
            if not body:
                continue
            if _looks_like_boundary_bullet(item):
                if not boundary_value:
                    boundary_value = body
                continue
            if body not in fact_values:
                fact_values.append(body)
            if len(fact_values) >= 2 and boundary_value:
                break
        if profile_kind == "installation_tutorial":
            lead = fact_values[0] if fact_values else "安装顺序和验证动作"
            tail = boundary_value or ("当前证据不完整" if evidence.coverage == "partial" else "更适合作为安装参考")
            return f"这条内容主要在说明{subject or '安装流程'}，当前可确认的关键动作是{lead}；{tail}。"
        if profile_kind == "skill_recommendation":
            lead = fact_values[0] if fact_values else "安装入口和使用方式"
            tail = boundary_value or ("当前证据不完整" if evidence.coverage == "partial" else "更适合作为技能筛选与安装参考")
            return f"这条内容围绕{subject or '一个技能/工具'}，当前能确认的是{lead}；{tail}。"
        lead = fact_values[0] if fact_values else "当前只抽到少量可核对信息"
        if len(fact_values) >= 2:
            lead = f"{fact_values[0]}，并补充了{fact_values[1]}"
        tail = boundary_value or ("当前证据不完整" if evidence.coverage == "partial" else "更适合作为初筛参考")
        if subject:
            return f"这条内容主要在讲{subject}，当前能确认的是{lead}；{tail}。"
        return f"当前能确认的是{lead}；{tail}。"
    return clean


def _normalize_requirement_token(value: str) -> str:
    text = _sanitize_display_url(str(value).strip()).lower()
    return re.sub(r"\s+", " ", text)


def _looks_like_fragmented_video_bullet(value: str, evidence: EvidenceBundle) -> bool:
    text = _normalize_bullet_text(value)
    if not text:
        return True
    body = re.sub(r"^\d{1,2}\.\s*", "", text).strip()
    if not body:
        return True
    lowered = body.lower()
    if any(token in lowered for token in ["spm_id_from", "search-card", "vd_source"]):
        return True
    if re.match(r"^[¥$€£]?\d", body):
        return True
    if re.fullmatch(r"[0-9a-z._?&=:/+-]{12,}", lowered):
        return True
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", body))
    digit_chars = len(re.findall(r"\d", body))
    punctuation = len(re.findall(r"[，。；：,.!?！？]", body))
    transcript_corpus = re.sub(r"\s+", "", ((evidence.transcript or "") + "\n" + (evidence.text or "")).lower())
    compact = re.sub(r"\s+", "", lowered)
    if compact and len(compact) >= 20 and compact in transcript_corpus and punctuation == 0:
        return True
    if digit_chars >= max(4, cjk_chars) and punctuation <= 1:
        return True
    if cjk_chars < 8:
        return True
    return False


def _timeline_section_bullets(timeline_sections: list[dict[str, object]], limit: int = 6) -> list[str]:
    bullets: list[str] = []
    for raw in timeline_sections:
        if not isinstance(raw, dict):
            continue
        heading = re.sub(r"\s+", " ", str(raw.get("heading", "")).strip())
        section_summary = re.sub(r"\s+", " ", str(raw.get("summary", "")).strip())
        if not section_summary:
            continue
        if heading and heading not in section_summary:
            text = f"{heading}：{section_summary}"
        else:
            text = section_summary
        text = _normalize_bullet_text(text)
        if text and text not in bullets:
            bullets.append(text)
        if len(bullets) >= limit:
            break
    return bullets[:limit]


def _should_replace_video_bullets_from_timeline(
    bullets: list[str],
    timeline_sections: list[dict[str, object]],
    evidence: EvidenceBundle,
) -> bool:
    if evidence.source_kind != "video_url" or not timeline_sections:
        return False
    meaningful = [item for item in bullets if _normalize_bullet_text(item)]
    if len(meaningful) < 3:
        return True
    bad_count = sum(1 for item in meaningful if _looks_like_fragmented_video_bullet(item, evidence))
    return bad_count >= max(2, len(meaningful) // 2)


def _missing_required_fields(
    *,
    title: str,
    conclusion: str,
    bullets: list[str],
    evidence_quotes: list[str],
    follow_up_actions: list[str],
    evidence: EvidenceBundle,
) -> list[str]:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    profile = metadata.get("content_profile", {}) if isinstance(metadata.get("content_profile"), dict) else {}
    if not profile:
        profile = infer_content_profile(evidence.source_kind, evidence.source_url, evidence.text, metadata)
    signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
    corpus = "\n".join([title, conclusion, evidence.source_url or "", *bullets, *evidence_quotes, *follow_up_actions]).lower()
    missing: list[str] = []
    for key, value in iter_required_signal_entries(profile, signals):
        normalized = _normalize_requirement_token(value)
        if normalized and normalized not in corpus:
            missing.append(f"{key}:{value}")
    if profile.get("require_action_checklist"):
        if len(follow_up_actions) < 2:
            missing.append("section:执行清单")
        action_corpus = "\n".join(follow_up_actions).lower()
        commands = signals.get("commands", []) if isinstance(signals.get("commands"), list) else []
        validations = signals.get("validation_actions", []) if isinstance(signals.get("validation_actions"), list) else []
        if commands and not any(_normalize_requirement_token(item) in action_corpus for item in commands[:1]):
            missing.append("actions:关键命令")
        if validations and not any(_normalize_requirement_token(item) in action_corpus for item in validations[:1]):
            missing.append("actions:验证动作")
    if profile.get("require_project_section"):
        has_project_link = any(
            line.startswith(("项目名称:", "GitHub地址:", "关键链接:", "视频链接:", "技能名:", "技能ID:"))
            for line in bullets
        )
        if not has_project_link:
            missing.append("section:项目与链接")
    return missing


def _validate_and_normalize_summary(summary: SummaryResult, evidence: EvidenceBundle) -> SummaryResult:
    title = _refine_title(summary.title, evidence)
    primary_topic = (summary.primary_topic or "").strip() or "未分类"
    secondary_topics = _normalize_list(list(summary.secondary_topics), limit=6)
    entities = _normalize_list(list(summary.entities), limit=12)
    conclusion = (summary.conclusion or "").strip()
    if not conclusion:
        raise RuntimeError("invalid summary response: missing conclusion")

    bullets = _refine_bullets(list(summary.bullets), evidence)
    if not bullets:
        raise RuntimeError("invalid summary response: missing bullets")

    evidence_quotes = _normalize_list(list(summary.evidence_quotes), limit=5)
    if not evidence_quotes:
        evidence_quotes = bullets[:2]

    coverage = _normalize_coverage(summary.coverage, evidence.coverage)
    confidence = _normalize_confidence(summary.confidence)
    timeliness = _normalize_level(summary.timeliness, fallback="medium")
    effectiveness = _normalize_level(summary.effectiveness, fallback="medium")
    recommendation_level = _normalize_recommendation_level(summary.recommendation_level)
    reader_judgment = re.sub(r"\s+", " ", str(summary.reader_judgment or "").strip())
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    video_gate = metadata.get("video_gate_reasons") if isinstance(metadata, dict) else None
    quality_gate = metadata.get("quality_gate", {}) if isinstance(metadata.get("quality_gate"), dict) else {}
    gate_mode = str(quality_gate.get("mode", "")).strip().lower()
    gate_outcome = str(quality_gate.get("outcome", "")).strip().lower()
    if isinstance(video_gate, list) and video_gate:
        coverage = "partial"
        confidence = "medium" if confidence == "high" else confidence
    refined_conclusion = _refine_conclusion(conclusion, evidence, bullets)
    should_append_incomplete = not (gate_mode == "probe" and gate_outcome == "partial")
    if isinstance(video_gate, list) and video_gate and should_append_incomplete and "证据不完整" not in refined_conclusion:
        refined_conclusion = refined_conclusion.rstrip("。") + "，当前证据不完整。"
    if not reader_judgment:
        profile_kind = ""
        if isinstance(evidence.metadata, dict):
            profile = evidence.metadata.get("content_profile", {})
            if isinstance(profile, dict):
                profile_kind = str(profile.get("kind", "")).strip()
        if evidence.source_kind == "video_url":
            reader_judgment = "更适合先看提炼结果，再决定是否需要回看原视频。"
        elif _is_docs_overview_page(evidence):
            reader_judgment = "当前页只够判断支持范围和大致接入方向，不够直接拿来安装。"
        elif profile_kind == "installation_tutorial":
            reader_judgment = "适合直接当安装参考，但环境与验证步骤仍建议回原文核对。"
        elif profile_kind == "project_overview":
            reader_judgment = "适合作为项目初筛，真正落地前还要回仓库或文档确认细节。"
        else:
            reader_judgment = "当前可先用于初筛，后续是否继续投入取决于证据密度和实际需求。"
    if isinstance(video_gate, list) and video_gate:
        recommendation_level = "optional" if recommendation_level == "must_read" else recommendation_level
        effectiveness = "medium" if effectiveness == "high" else effectiveness
    note_tags = _normalize_list(list(summary.note_tags), limit=8)
    follow_up_actions = _refine_follow_up_actions(list(summary.follow_up_actions), evidence, bullets)
    outcome = (summary.outcome or "").strip().lower()
    if outcome not in {"summarized", "partial", "refused"}:
        outcome = "partial" if coverage == "partial" else "summarized"
    evidence_basis = _normalize_list(list(summary.evidence_basis), limit=10)
    uncertainties = _normalize_list(list(summary.uncertainties), limit=8)
    if evidence.source_kind == "video_url" and outcome == "partial":
        quality_gate = metadata.get("quality_gate", {}) if isinstance(metadata.get("quality_gate"), dict) else {}
        expected_outline_count = int(quality_gate.get("expected_outline_count", 0) or 0)
        if expected_outline_count > 0:
            numbered_count = len(
                [
                    item
                    for item in bullets
                    if re.match(r"^\d{1,2}\.\s+", str(item).strip())
                ]
            )
            retained_outline_count = min(
                expected_outline_count,
                numbered_count if numbered_count > 0 else len(bullets),
            )
            if re.search(r"当前已覆盖\s+\d+/\d+\s*项", refined_conclusion):
                refined_conclusion = re.sub(
                    r"当前已覆盖\s+\d+/\d+\s*项",
                    f"当前已覆盖 {retained_outline_count}/{expected_outline_count} 项",
                    refined_conclusion,
                )
            updated_uncertainties: list[str] = []
            for item in uncertainties:
                text = str(item)
                if re.search(r"^仅覆盖\s+\d+/\d+\s*项", text):
                    text = re.sub(
                        r"^仅覆盖\s+\d+/\d+\s*项",
                        f"仅覆盖 {retained_outline_count}/{expected_outline_count} 项",
                        text,
                    )
                updated_uncertainties.append(text)
            uncertainties = _normalize_list(updated_uncertainties, limit=8)
    timeline_sections: list[dict[str, object]] = []
    for raw in list(summary.timeline_sections or [])[:12]:
        if not isinstance(raw, dict):
            continue
        heading = re.sub(r"\s+", " ", str(raw.get("heading", "")).strip())
        section_summary = re.sub(r"\s+", " ", str(raw.get("summary", "")).strip())
        section_bullets = raw.get("bullets", [])
        if not isinstance(section_bullets, list):
            section_bullets = raw.get("key_points", [])
        if not isinstance(section_bullets, list):
            section_bullets = []
        evidence_lines = raw.get("evidence", [])
        if not isinstance(evidence_lines, list):
            evidence_lines = []
        timeline_sections.append(
            {
                "start": raw.get("start"),
                "end": raw.get("end"),
                "heading": heading,
                "summary": section_summary,
                "bullets": _normalize_list([_normalize_bullet_text(str(item)) for item in section_bullets], limit=4),
                "evidence": _normalize_list([str(item) for item in evidence_lines], limit=3),
            }
        )
    if _should_replace_video_bullets_from_timeline(bullets, timeline_sections, evidence):
        rebuilt_bullets = _timeline_section_bullets(timeline_sections, limit=6)
        if rebuilt_bullets:
            bullets = rebuilt_bullets
            if not evidence_quotes:
                evidence_quotes = bullets[:2]
    refusal_reason = re.sub(r"\s+", " ", str(summary.refusal_reason or "").strip())
    finance_matrix: list[dict[str, str]] = []
    if isinstance(summary.finance_matrix, list):
        seen_finance_rows: set[str] = set()
        for raw in summary.finance_matrix[:12]:
            if not isinstance(raw, dict):
                continue
            name = re.sub(r"\s+", " ", str(raw.get("name", "")).strip())
            if not name:
                continue
            key = name.lower()
            if key in seen_finance_rows:
                continue
            seen_finance_rows.add(key)
            row = {
                "name": name,
                "sector": _normalize_finance_cell_text(str(raw.get("sector", ""))),
                "thesis": _normalize_finance_cell_text(str(raw.get("thesis", ""))),
                "position_change": _normalize_finance_cell_text(str(raw.get("position_change", ""))),
                "risk": _normalize_finance_cell_text(str(raw.get("risk", ""))),
            }
            if any(row[field] for field in ["thesis", "position_change", "risk"]):
                finance_matrix.append(row)
    finance_snapshot: dict[str, list[str]] = {}
    if isinstance(summary.finance_snapshot, dict):
        for key in ["market_view", "performance_review", "action_plan"]:
            value = summary.finance_snapshot.get(key, [])
            if isinstance(value, str):
                values = re.split(r"[。\n；;]+", value)
            elif isinstance(value, list):
                values = [str(item) for item in value]
            else:
                values = []
            lines = _normalize_list([re.sub(r"\s+", " ", str(item).strip()).strip("。；;") for item in values], limit=4)
            if lines:
                finance_snapshot[key] = lines
    uncertainties = [
        item
        for item in uncertainties
        if str(item).strip().lower() not in {"无", "none", "n/a", "na"}
    ]
    missing_required = _missing_required_fields(
        title=title,
        conclusion=refined_conclusion,
        bullets=bullets,
        evidence_quotes=evidence_quotes,
        follow_up_actions=follow_up_actions,
        evidence=evidence,
    )
    if missing_required:
        raise RuntimeError("invalid summary response: missing required fields: " + ", ".join(missing_required[:6]))

    return SummaryResult(
        title=title,
        primary_topic=primary_topic,
        secondary_topics=secondary_topics,
        entities=entities,
        conclusion=refined_conclusion,
        bullets=bullets,
        evidence_quotes=evidence_quotes,
        coverage=coverage,
        confidence=confidence,
        note_tags=note_tags,
        follow_up_actions=follow_up_actions,
        timeliness=timeliness,
        effectiveness=effectiveness,
        recommendation_level=recommendation_level,
        reader_judgment=reader_judgment,
        outcome=outcome,
        evidence_basis=evidence_basis,
        timeline_sections=timeline_sections,
        uncertainties=uncertainties,
        refusal_reason=refusal_reason,
        finance_matrix=finance_matrix,
        finance_snapshot=finance_snapshot,
    )
