"""OpenAI-compatible conservative summarizer client."""

from __future__ import annotations

import json
import re
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from .config import SummarizerConfig
from .content_profile import infer_content_profile, iter_required_signal_entries
from .models import EvidenceBundle, SummaryResult


PROMPT = """You summarize captured knowledge for an Obsidian knowledge base.
Only use facts present in the evidence. Do not infer unstated facts.
Return strict JSON with these keys:
title, primary_topic, secondary_topics, entities, conclusion, bullets,
evidence_quotes, coverage, confidence, note_tags, follow_up_actions

Rules:
- Write in Chinese.
- Style must be professional, concise, and objective (research-note tone).
- Never use second-person wording such as "你/你可以/对你有用".
- coverage must be one of: full, partial
- confidence must be one of: high, medium, low
- title must be short and semantic (no site UI prefix like "GitHub -" / "小红书 -").
- conclusion must be one sentence and directly state the main finding.
- bullets should be 3 to 6 concise points, each point only one fact.
- follow_up_actions should be 2 to 6 executable checklist items when content is setup/install/tutorial.
- evidence_quotes should be short phrases copied from the evidence
- If the evidence looks like a tutorial (contains steps such as "一、" / "二、" or "步骤"), bullets must reflect the actual steps.
- If the evidence is setup/install tutorial, ensure output contains: prerequisites, key commands, validation checkpoint.
- If evidence is incomplete, set coverage=partial and say so in conclusion.
- If the evidence is about a Skill/tool recommendation, prioritize: skill name, source link, install/use method, required model or token cost notes.
- Do not miss GitHub links, command lines, hashtags, or explicit "Skill" names if present in evidence.
- If a skill slug/id appears (for example `tech-earnings-deepdive`), include it as a key point.
- For GitHub links, prioritize README facts: what it is, how to install/use, and exact repo URL.
- The first 1-2 bullets should be the highest-value actionable facts.
- If `metadata.content_profile.kind=skill_recommendation`, must include: skill name, skill id, repo/source link, install method, use method.
- If `metadata.content_profile.kind=installation_tutorial`, must include: prerequisites, key commands, validation step.
- If `metadata.content_profile.kind=project_overview`, must include: project name, repo/doc link, core purpose, run/use boundary when present.
- Prefer concise "fact-first" style similar to technical research notes.
- Avoid boilerplate/meta statements like "已提取核心事实" or "帮助你快速理解".
- Avoid vague phrases like "内容完整/覆盖全面/适用于开发者和爱好者" unless the evidence explicitly states them.
- Do not repeat the same fact in different bullets with slight wording changes.
"""


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
    if use_cases:
        bullets.append("使用方式: " + " | ".join(use_cases[:2]))
    elif purposes:
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
    tutorial_like = _is_tutorial_like(evidence)
    if tutorial_like:
        install_actions = _extract_install_actions_from_evidence(evidence, limit=8)
        merged = _normalize_list(install_actions + normalized, limit=8)
        if len(merged) < 2:
            merged = _normalize_list(merged + bullets[:3], limit=5)
        return merged[:6]
    return normalized[:4]


def _refine_conclusion(conclusion: str, evidence: EvidenceBundle) -> str:
    clean = re.sub(r"\s+", " ", (conclusion or "").strip())
    if not clean:
        return clean
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
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    video_gate = metadata.get("video_gate_reasons") if isinstance(metadata, dict) else None
    if isinstance(video_gate, list) and video_gate:
        coverage = "partial"
        confidence = "medium" if confidence == "high" else confidence
    refined_conclusion = _refine_conclusion(conclusion, evidence)
    if isinstance(video_gate, list) and video_gate and "证据不完整" not in refined_conclusion:
        refined_conclusion = refined_conclusion.rstrip("。") + "，当前证据不完整。"
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
    )
