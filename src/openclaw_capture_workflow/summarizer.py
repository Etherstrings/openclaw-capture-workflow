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


PROMPT = """Role: J.A.R.V.I.S. (Just A Rather Very Intelligent System)

Profile:
You are an advanced AI butler designed by Tony Stark.
Your job is to help a busy software engineer at a large tech company process fragmented web information.
You are not merely a summarizer. You are a calm, precise digital butler with reconstruction, reasoning, and prioritization abilities.

Core mission:
- reconstruct fragmented evidence into a coherent understanding
- decide what matters and what does not
- explain the current situation in plain, competent language
- surface what Sir/Miss should pay attention to next

Capabilities:
- Semantic reconstruction: the evidence may be sliced, partially OCR'd, noisy, or structurally broken. Rebuild the likely logic chain.
- Multi-mode summarization: produce an executive brief, technical note, or action-oriented read depending on the source.
- Tactical judgment: estimate timeliness, practical usefulness, and recommendation level for a busy engineer.
- Dry humor: maintain professional composure with a faint J.A.R.V.I.S.-style tone. Use "Sir" or "Miss" sparingly when natural in `reader_judgment`.

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
- Write like a highly competent butler briefing a busy engineer: calm, direct, useful.
- Prioritize this order: what this is, why it matters, what action follows, and whether it deserves attention now.
- Do not waste words on filler like "这是一个很好的问题".
- Avoid stiff report language and empty packaging.
- Never use second-person wording such as "你/你可以/对你有用" in the structured summary fields.
- coverage must be one of: full, partial
- confidence must be one of: high, medium, low
- timeliness must be one of: high, medium, low
- effectiveness must be one of: high, medium, low
- recommendation_level must be one of: must_read, recommended, optional, skip
- reader_judgment must be one sentence stating the call for a large-tech software engineer.
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
"""

PROMPT_VERSION = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()[:16]


class SummaryEngine(Protocol):
    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
        ...


class OpenAICompatibleSummarizer:
    def __init__(self, config: SummarizerConfig) -> None:
        self.config = config

    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
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


def _normalize_bullet_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value).strip())
    text = text.strip("。；;")
    # Keep full URLs for traceability; truncating URLs breaks downstream recall checks.
    max_len = 220 if ("http://" in text or "https://" in text) else 120
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "..."
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


def _signal_priority_bullets(evidence: EvidenceBundle, limit: int = 4) -> list[str]:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    signals = metadata.get("signals", {})
    if not isinstance(signals, dict):
        return []
    bullets: list[str] = []
    projects = [str(item) for item in signals.get("projects", []) if str(item).strip()]
    links = [_sanitize_display_url(str(item)) for item in signals.get("links", []) if str(item).strip()]
    skills = [str(item) for item in signals.get("skills", []) if str(item).strip()]
    skill_ids = [str(item) for item in signals.get("skill_ids", []) if str(item).strip()]
    commands = [str(item) for item in signals.get("commands", []) if str(item).strip()]
    prerequisites = [str(item) for item in signals.get("prerequisites", []) if str(item).strip()]
    validations = [str(item) for item in signals.get("validation_actions", []) if str(item).strip()]
    use_cases = [str(item) for item in signals.get("use_cases", []) if str(item).strip()]
    purposes = [str(item) for item in signals.get("purposes", []) if str(item).strip()]

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

    if projects:
        bullets.append("项目名称: " + " | ".join(projects[:1]))
    if github_links:
        bullets.append("GitHub地址: " + " | ".join(github_links[:2]))
    if video_links:
        bullets.append("视频链接: " + " | ".join(video_links[:2]))
    if other_links:
        bullets.append("关键链接: " + " | ".join(other_links[:2]))
    if skills:
        bullets.append("技能名: " + " | ".join(skills[:2]))
    if skill_ids:
        bullets.append("技能ID: " + " | ".join(skill_ids[:3]))
    if commands:
        has_install_command = any(token in cmd.lower() for cmd in commands for token in ["install", "/install-skill"])
        if has_install_command:
            bullets.append("安装方法: " + " | ".join(commands[:2]))
        else:
            bullets.append("关键命令: " + " | ".join(commands[:2]))
    if prerequisites:
        bullets.append("前置条件: " + " | ".join(prerequisites[:2]))
    if validations:
        bullets.append("验证动作: " + " | ".join(validations[:2]))
    if use_cases and evidence.source_kind != "video_url":
        bullets.append("使用方式: " + " | ".join(use_cases[:2]))
    elif purposes and evidence.source_kind != "video_url":
        bullets.append("核心用途: " + " | ".join(purposes[:2]))
    return _normalize_list([_normalize_bullet_text(item) for item in bullets], limit=limit)


def _dedupe_fact_categories(items: list[str]) -> list[str]:
    category_labels = {
        "项目名称",
        "GitHub地址",
        "视频链接",
        "关键链接",
        "技能名",
        "技能ID",
        "关键命令",
        "安装方法",
        "前置条件",
        "验证动作",
        "使用方式",
        "核心用途",
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


def _refine_bullets(summary_bullets: list[str], evidence: EvidenceBundle) -> list[str]:
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
        video_facts = _extract_video_fact_points(bullets, evidence, limit=3)
        derived: list[str] = []
        joined = "\n".join(video_facts + [str(evidence.metadata.get("user_guidance", ""))]).lower() if isinstance(evidence.metadata, dict) else "\n".join(video_facts).lower()
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
        if any(token in joined for token in ["仪表盘", "数据源"]):
            derived.append("按自己的关注主题调整仪表盘和数据源")
        merged = _normalize_list(filtered + derived, limit=4)
        return merged[:4]
    if _is_incomplete_video(evidence):
        guided = [
            "补抓字幕或语音轨后再复核结论",
            "回到原视频确认完整上下文",
        ]
        merged = _normalize_list(guided + normalized, limit=4)
        return merged[:4]
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
    signals = {}
    if isinstance(evidence.metadata, dict) and isinstance(evidence.metadata.get("signals"), dict):
        signals = evidence.metadata["signals"]
    projects = signals.get("projects", []) if isinstance(signals, dict) else []
    skills = signals.get("skills", []) if isinstance(signals, dict) else []
    skill_ids = signals.get("skill_ids", []) if isinstance(signals, dict) else []
    if _is_generic_bullet(clean) or clean in {"已提取核心事实。", "已提取核心事实"}:
        parts: list[str] = []
        if projects:
            parts.append(f"识别到项目 {projects[0]}")
        if skills:
            parts.append(f"技能为 {skills[0]}")
        if skill_ids:
            parts.append(f"技能ID {skill_ids[0]}")
        if evidence.coverage == "partial":
            parts.append("证据不完整")
        elif not parts:
            parts.append("已提取核心事实")
        return "，".join(parts) + "。"
    return clean


def _normalize_requirement_token(value: str) -> str:
    text = _sanitize_display_url(str(value).strip()).lower()
    return re.sub(r"\s+", " ", text)


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
    corpus = "\n".join([title, conclusion, *bullets, *evidence_quotes, *follow_up_actions]).lower()
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
    if isinstance(video_gate, list) and video_gate:
        coverage = "partial"
        confidence = "medium" if confidence == "high" else confidence
    refined_conclusion = _refine_conclusion(conclusion, evidence, bullets)
    if isinstance(video_gate, list) and video_gate and "证据不完整" not in refined_conclusion:
        refined_conclusion = refined_conclusion.rstrip("。") + "，当前证据不完整。"
    if not reader_judgment:
        profile_kind = ""
        if isinstance(evidence.metadata, dict):
            profile = evidence.metadata.get("content_profile", {})
            if isinstance(profile, dict):
                profile_kind = str(profile.get("kind", "")).strip()
        if evidence.source_kind == "video_url":
            reader_judgment = "从大厂程序员视角看，这条内容更适合用来快速筛选是否值得后续回看。"
        elif profile_kind == "installation_tutorial":
            reader_judgment = "从大厂程序员视角看，这条内容偏实用，适合直接留作后续操作参考。"
        else:
            reader_judgment = "从大厂程序员视角看，这条内容有信息价值，但是否深入跟进取决于当前任务相关性。"
    if isinstance(video_gate, list) and video_gate:
        recommendation_level = "optional" if recommendation_level == "must_read" else recommendation_level
        effectiveness = "medium" if effectiveness == "high" else effectiveness
    note_tags = _normalize_list(list(summary.note_tags), limit=8)
    follow_up_actions = _refine_follow_up_actions(list(summary.follow_up_actions), evidence, bullets)
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
    )
