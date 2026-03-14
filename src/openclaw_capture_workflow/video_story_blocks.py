"""Video story block helpers for narrative-style summaries."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .models import EvidenceBundle


ALLOWED_VIDEO_STORY_BLOCK_LABELS = (
    "core_topic",
    "workflow",
    "implementation",
    "risk",
    "viewer_feedback",
    "other",
)

_LABEL_ORDER = {
    "core_topic": 0,
    "workflow": 1,
    "implementation": 2,
    "risk": 3,
    "viewer_feedback": 4,
    "other": 5,
}

_PLACEHOLDER_SUMMARY_PATTERNS = [
    re.compile(r"^视频主要围绕《.+》展开，重点说明其中的核心做法。?$"),
    re.compile(r"^视频核心是在讲《.+》的使用方法和落地流程。?$"),
    re.compile(r"^视频核心是在讲《.+》的玩法思路和关键套路。?$"),
    re.compile(r"^视频把核心流程拆成了几个连续环节，从输入材料到结果输出都有交代。?$"),
    re.compile(r"^视频还补充了若干细节和示例，帮助理解真正落地时会遇到什么问题。?$"),
]


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _unique_list(values: list[str], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _metadata_dict(evidence: EvidenceBundle) -> dict[str, Any]:
    return evidence.metadata if isinstance(evidence.metadata, dict) else {}


def _metadata_lines(metadata: dict[str, Any], key: str, *, limit: int) -> list[str]:
    values = metadata.get(key, [])
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for item in values[:limit]:
        text = _clean_text(item)
        if text:
            result.append(text)
    return result


def _strip_timestamp(value: str) -> str:
    return re.sub(r"^\[[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?\]\s*", "", _clean_text(value))


def _title_topic(value: str) -> str:
    title = _clean_text(value)
    title = re.sub(r"[_\-\s]*哔哩哔哩[_\-\s]*bilibili$", "", title, flags=re.IGNORECASE).strip(" -_|")
    title = re.sub(r"【[^】]{0,24}】", "", title).strip()
    title = re.sub(r"\[[^\]]{0,24}\]", "", title).strip()
    if len(title) > 30:
        title = title[:30].rstrip()
    return title or "该视频"


def _normalize_story_block(value: Any) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    label = _clean_text(value.get("label"))
    if label not in ALLOWED_VIDEO_STORY_BLOCK_LABELS:
        return None
    summary = _clean_text(value.get("summary"))
    if not summary:
        return None
    evidence_items = value.get("evidence", [])
    if not isinstance(evidence_items, list):
        evidence_items = []
    normalized_evidence = _unique_list([_clean_text(item) for item in evidence_items], limit=3)
    return {
        "label": label,
        "summary": summary,
        "evidence": normalized_evidence,
    }


def get_viewer_feedback(evidence: EvidenceBundle) -> list[str]:
    metadata = _metadata_dict(evidence)
    feedback = metadata.get("viewer_feedback", [])
    if not isinstance(feedback, list):
        return []
    return _unique_list([_clean_text(item) for item in feedback], limit=5)


def get_video_story_blocks(evidence: EvidenceBundle) -> list[dict[str, object]]:
    if evidence.source_kind != "video_url":
        return []
    metadata = _metadata_dict(evidence)
    stored = metadata.get("video_story_blocks", [])
    if isinstance(stored, list) and stored:
        normalized = [_normalize_story_block(item) for item in stored]
        blocks = [item for item in normalized if item]
        if blocks:
            return sorted(blocks, key=lambda item: (_LABEL_ORDER.get(str(item["label"]), 99), str(item["summary"])))
    return build_video_story_blocks(evidence)


def has_rich_video_story_evidence(evidence: EvidenceBundle) -> bool:
    if evidence.source_kind != "video_url":
        return False
    metadata = _metadata_dict(evidence)
    for key in ["timeline_highlights", "transcript_timeline_lines", "subtitle_timeline_lines", "keyframe_ocr_lines"]:
        values = metadata.get(key, [])
        if isinstance(values, list) and any(_clean_text(item) for item in values):
            return True
    return bool(get_viewer_feedback(evidence))


def _is_placeholder_story_summary(summary: str) -> bool:
    text = _clean_text(summary)
    if not text:
        return True
    return any(pattern.match(text) for pattern in _PLACEHOLDER_SUMMARY_PATTERNS)


def story_blocks_are_qualified(blocks: list[dict[str, object]]) -> bool:
    non_feedback = [block for block in blocks if str(block.get("label", "")).strip() != "viewer_feedback"]
    if len(non_feedback) < 3:
        return False
    meaningful = 0
    for block in non_feedback:
        summary = _clean_text(block.get("summary"))
        if summary and not _is_placeholder_story_summary(summary):
            meaningful += 1
    return meaningful >= 2


def get_qualified_video_story_blocks(evidence: EvidenceBundle) -> list[dict[str, object]]:
    blocks = get_video_story_blocks(evidence)
    return blocks if story_blocks_are_qualified(blocks) else []


def get_story_block_outline_points(evidence: EvidenceBundle, *, include_feedback: bool = False) -> list[str]:
    points: list[str] = []
    for block in get_qualified_video_story_blocks(evidence):
        label = str(block.get("label", "")).strip()
        if not include_feedback and label == "viewer_feedback":
            continue
        summary = _clean_text(block.get("summary"))
        if summary and summary not in points:
            points.append(summary)
    return points[:12]


def get_story_block_bullets(evidence: EvidenceBundle, *, include_feedback: bool = True, limit: int = 6) -> list[str]:
    bullets: list[str] = []
    for block in get_qualified_video_story_blocks(evidence):
        label = str(block.get("label", "")).strip()
        if not include_feedback and label == "viewer_feedback":
            continue
        summary = _clean_text(block.get("summary"))
        if summary and summary not in bullets:
            bullets.append(summary)
        if len(bullets) >= limit:
            break
    return bullets[:limit]


def _pick_lines(
    lines: list[str],
    *,
    keywords: list[str],
    limit: int = 3,
    min_len: int = 6,
    max_len: int = 120,
    exclude: list[str] | None = None,
) -> list[str]:
    excluded = {_clean_text(item) for item in (exclude or []) if _clean_text(item)}
    result: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        line = _clean_text(raw)
        if not line:
            continue
        line = _strip_timestamp(line)
        if line in excluded or line in seen:
            continue
        if len(line) < min_len or len(line) > max_len:
            continue
        lowered = line.lower()
        if not any(token in line or token in lowered for token in keywords):
            continue
        seen.add(line)
        result.append(line)
        if len(result) >= limit:
            break
    return result[:limit]


def _fallback_line(lines: list[str], *, exclude: list[str] | None = None) -> list[str]:
    excluded = {_clean_text(item) for item in (exclude or []) if _clean_text(item)}
    for raw in lines:
        line = _strip_timestamp(raw)
        if not line or line in excluded:
            continue
        if 8 <= len(line) <= 120:
            return [line]
    return []


def _candidate_lines(evidence: EvidenceBundle) -> list[str]:
    metadata = _metadata_dict(evidence)
    lines: list[str] = []
    title = _clean_text(metadata.get("bilibili_title") or evidence.title or "")
    desc = _clean_text(metadata.get("bilibili_description") or "")
    if title:
        lines.append(f"标题: {title}")
    if desc:
        lines.append(f"简介: {desc}")
    lines.extend(_metadata_lines(metadata, "timeline_highlights", limit=10))
    lines.extend(_metadata_lines(metadata, "transcript_timeline_lines", limit=20))
    lines.extend(_metadata_lines(metadata, "subtitle_timeline_lines", limit=20))
    lines.extend(_metadata_lines(metadata, "keyframe_ocr_lines", limit=12))
    lines.extend(_metadata_lines(metadata, "keyframe_text", limit=8))
    return _unique_list(lines, limit=50)


def _core_topic_block(title: str, desc: str, corpus: str, lines: list[str]) -> dict[str, object] | None:
    lowered = corpus.lower()
    topic = _title_topic(title)
    finance_tokens = ["股票", "股市", "自选股", "开盘", "买入", "持有", "量化", "交易", "行情"]
    game_tokens = ["攻略", "流派", "构筑", "打法", "卡组", "英雄", "boss", "角色"]
    tutorial_tokens = ["教程", "安装", "部署", "配置", "工作流", "自动化", "接入", "运行"]
    if "openclaw" in lowered and any(token in corpus for token in finance_tokens):
        summary = "视频核心是在演示用 OpenClaw 做股票量化分析，并生成每日交易建议。"
    elif any(token in corpus for token in game_tokens):
        summary = f"视频核心是在讲《{topic}》的玩法思路和关键套路。"
    elif any(token in corpus for token in tutorial_tokens):
        summary = f"视频核心是在讲《{topic}》的使用方法和落地流程。"
    else:
        summary = f"视频主要围绕《{topic}》展开，重点说明其中的核心做法。"
    evidence = _unique_list(
        [f"标题: {title}" if title else "", f"简介: {desc}" if desc else ""] + _fallback_line(lines),
        limit=3,
    )
    return {"label": "core_topic", "summary": summary, "evidence": evidence}


def _workflow_block(corpus: str, lines: list[str]) -> dict[str, object] | None:
    finance_tokens = ["自选股", "股票", "买入", "持有", "开盘", "分析", "推荐", "决策"]
    tutorial_tokens = ["安装", "部署", "配置", "运行", "接入", "步骤", "命令"]
    game_tokens = ["构筑", "流派", "打法", "资源", "卡牌", "路线"]
    evidence = _pick_lines(
        lines,
        keywords=[
            "自选股",
            "股票",
            "开盘",
            "买入",
            "持有",
            "分析",
            "推荐",
            "步骤",
            "部署",
            "配置",
            "运行",
            "构筑",
            "打法",
        ],
        limit=3,
    )
    if not evidence:
        return None
    if "openclaw" in corpus.lower() and any(token in corpus for token in finance_tokens):
        summary = "流程是把自选股列表交给 OpenClaw，系统会在开盘前给出逐只股票的分析和买入/持有建议。"
    elif any(token in corpus for token in tutorial_tokens):
        summary = "视频把关键流程拆成了输入、配置和运行几个环节，重点在把方案真正跑起来。"
    elif any(token in corpus for token in game_tokens):
        summary = "视频按构筑思路、关键资源和实战处理顺序来讲解整个打法。"
    else:
        summary = "视频把核心流程拆成了几个连续环节，从输入材料到结果输出都有交代。"
    return {"label": "workflow", "summary": summary, "evidence": evidence}


def _implementation_block(corpus: str, lines: list[str]) -> dict[str, object] | None:
    evidence = _pick_lines(
        lines,
        keywords=[
            "github",
            "服务器",
            "部署",
            "自动化",
            "工作流",
            "数据",
            "信源",
            "模型",
            "行情",
            "业绩",
            "api",
            "分析方式",
            "专业",
        ],
        limit=3,
    )
    if not evidence:
        return None
    lowered = corpus.lower()
    if any(token in lowered for token in ["github", "服务器", "部署", "自动化", "工作流"]):
        summary = "实现上依赖 GitHub、服务器或自动化工作流，把整套分析流程持续跑起来。"
    elif any(token in corpus for token in ["行情", "业绩", "数据", "信源", "专业"]):
        summary = "系统会结合行情、业绩和多种数据源来做判断，而不只是给一句结论。"
    else:
        summary = "实现层面强调了工具、数据或配置细节，说明这不是单纯口头推荐。"
    return {"label": "implementation", "summary": summary, "evidence": evidence}


def _risk_block(corpus: str, lines: list[str]) -> dict[str, object] | None:
    lowered = corpus.lower()
    evidence = _pick_lines(
        lines,
        keywords=[
            "别真跟",
            "图一乐",
            "谨慎",
            "风险",
            "不要盲目",
            "参考",
            "娱乐",
            "回本",
            "实盘",
        ],
        limit=3,
    )
    finance_markers = ["股票", "量化", "交易", "买入", "持有", "开盘", "自选股"]
    if not evidence and not any(token in corpus for token in finance_markers):
        return None
    if any(token in corpus for token in ["别真跟", "图一乐", "谨慎", "风险", "参考", "娱乐"]):
        summary = "视频明确提醒这更像技术展示和参考，不建议盲目跟单或直接照搬投资决策。"
    elif any(token in lowered for token in ["stock", "trade", "quant"]):
        summary = "内容涉及投资判断，实际操作仍需自行复核，不能把系统建议直接当成确定结论。"
    else:
        summary = "内容涉及投资判断，实际操作仍需自行复核，不能把系统建议直接当成确定结论。"
    if not evidence:
        evidence = _unique_list(
            [line for line in lines if any(token in line for token in finance_markers)],
            limit=3,
        )
    return {"label": "risk", "summary": summary, "evidence": evidence}


def _viewer_feedback_block(feedback: list[str]) -> dict[str, object] | None:
    if not feedback:
        return None
    joined = " ".join(feedback)
    positive = any(token in joined for token in ["挺准", "很准", "准", "回本", "大跌", "有意思", "厉害"])
    concern = any(token in joined for token in ["回本", "大跌", "风险", "亏", "谨慎"])
    curiosity = any(token in joined.lower() for token in ["openclaw", "api", "自动化", "行情", "信源"])
    if positive and concern:
        summary = "评论区一边在讨论信号准度，一边也拿回本和涨跌结果来检验这套方法是否靠谱。"
    elif curiosity:
        summary = "评论区主要在追问自动化交易、数据源和这套方案能否继续扩展到更多场景。"
    elif positive:
        summary = "评论区对这套自动化分析的效果和可用性表现出明显兴趣。"
    else:
        summary = "评论区主要围绕实盘体验、可靠性和使用边界展开讨论。"
    return {"label": "viewer_feedback", "summary": summary, "evidence": feedback[:3]}


def _other_block(corpus: str, lines: list[str], used_evidence: list[str]) -> dict[str, object] | None:
    evidence = _pick_lines(
        lines,
        keywords=[
            "每一只",
            "具体",
            "示例",
            "案例",
            "实测",
            "演示",
            "列出来",
            "准确",
            "回本",
            "大跌",
            "卡牌",
            "路线",
        ],
        limit=3,
        exclude=used_evidence,
    )
    if not evidence:
        evidence = _fallback_line(lines, exclude=used_evidence)
    if not evidence:
        return None
    if any(token in corpus for token in ["每一只", "具体的股票", "自选股", "股票"]):
        summary = "视频还给出了更细的逐项说明，强调结果不是只看一个总分或一句结论。"
    elif any(token in corpus for token in ["实测", "案例", "演示"]):
        summary = "视频还补充了示例和实测片段，用来说明方法落地后的实际表现。"
    else:
        summary = "视频还补充了若干细节和示例，帮助理解真正落地时会遇到什么问题。"
    return {"label": "other", "summary": summary, "evidence": evidence}


def build_video_story_blocks(evidence: EvidenceBundle) -> list[dict[str, object]]:
    if evidence.source_kind != "video_url":
        return []
    if not has_rich_video_story_evidence(evidence):
        return []
    metadata = _metadata_dict(evidence)
    title = _clean_text(metadata.get("bilibili_title") or evidence.title or "")
    desc = _clean_text(metadata.get("bilibili_description") or "")
    viewer_feedback = get_viewer_feedback(evidence)
    lines = _candidate_lines(evidence)
    corpus_parts = [title, desc, evidence.text or "", evidence.transcript or "", *lines, *viewer_feedback]
    corpus = "\n".join([part for part in corpus_parts if _clean_text(part)])

    blocks: list[dict[str, object]] = []
    core = _core_topic_block(title, desc, corpus, lines)
    if core:
        blocks.append(core)
    workflow = _workflow_block(corpus, lines)
    if workflow:
        blocks.append(workflow)
    implementation = _implementation_block(corpus, lines)
    if implementation:
        blocks.append(implementation)
    risk = _risk_block(corpus, lines)
    if risk:
        blocks.append(risk)

    used_evidence: list[str] = []
    for block in blocks:
        used_evidence.extend([_clean_text(item) for item in block.get("evidence", []) if _clean_text(item)])

    viewer_feedback_block = _viewer_feedback_block(viewer_feedback)
    if viewer_feedback_block:
        blocks.append(viewer_feedback_block)
        used_evidence.extend(viewer_feedback_block["evidence"])

    if len(blocks) < 3:
        other = _other_block(corpus, lines, used_evidence)
        if other:
            blocks.append(other)

    normalized = [_normalize_story_block(item) for item in blocks]
    result = [item for item in normalized if item]
    result = sorted(result, key=lambda item: (_LABEL_ORDER.get(str(item["label"]), 99), str(item["summary"])))
    if len(result) < 3:
        fallback = _other_block(corpus, lines, used_evidence)
        normalized_fallback = _normalize_story_block(fallback) if fallback else None
        if normalized_fallback and normalized_fallback not in result:
            result.append(normalized_fallback)
            result = sorted(result, key=lambda item: (_LABEL_ORDER.get(str(item["label"]), 99), str(item["summary"])))
    return result[:6]
