"""Helpers for note structure maps and link-safe naming."""

from __future__ import annotations

import re
from typing import Iterable, List

from .models import EvidenceBundle, SummaryResult


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "-", value).strip()
    return cleaned[:120] or "untitled"


def unique_topics(summary: SummaryResult) -> List[str]:
    seen = []
    for topic in [summary.primary_topic] + list(summary.secondary_topics):
        if topic and topic not in seen:
            seen.append(topic)
    return seen


def build_structure_map(summary: SummaryResult, evidence: EvidenceBundle, note_path: str, topic_links: Iterable[str], entity_links: Iterable[str]) -> str:
    bullets = summary.bullets[:3]
    lines = [
        "[结构总览]",
        f"├─ 主主题: {summary.primary_topic}",
        f"├─ 核心结论: {summary.conclusion}",
        "├─ 关键点",
    ]
    for index, bullet in enumerate(bullets):
        prefix = "└" if index == len(bullets) - 1 else "├"
        lines.append(f"│  {prefix}─ {bullet}")
    lines.extend(
        [
            "├─ 证据",
            f"│  ├─ 来源: {evidence.platform_hint or evidence.source_kind}",
            f"│  ├─ 完整度: {summary.coverage}",
            f"│  └─ 依据: {evidence.evidence_type}",
            "└─ 归档",
            f"   ├─ 主笔记: [[{note_path}]]",
            f"   ├─ 主题页: {' '.join(topic_links) if topic_links else '无'}",
            f"   └─ 实体: {' '.join(entity_links) if entity_links else '无'}",
        ]
    )
    return "\n".join(lines)
