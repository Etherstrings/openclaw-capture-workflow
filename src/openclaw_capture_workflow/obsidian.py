"""Obsidian note writing and topic/entity link management."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Dict, List
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .config import ObsidianConfig
from .models import EvidenceBundle, SummaryResult
from .note_renderer import NoteRenderEngine, build_note_materials, save_materials_file
from .note_graph import build_structure_map, safe_name, unique_topics


class ObsidianWriter:
    def __init__(
        self,
        config: ObsidianConfig,
        renderer: NoteRenderEngine | None = None,
        materials_root: Path | None = None,
    ) -> None:
        self.config = config
        self.renderer = renderer
        self.vault_path = Path(config.vault_path).expanduser()
        self.vault_name = self.vault_path.name
        self.topic_whitelist = set(config.auto_topic_whitelist)
        self.topic_blocklist = tuple(config.auto_topic_blocklist)
        self.materials_root = materials_root or (self.vault_path.parent / "state" / "materials")

    def write(self, summary: SummaryResult, evidence: EvidenceBundle, use_model_render: bool = False) -> Dict[str, object]:
        note_rel = self._resolve_note_rel(summary, evidence)
        inbox_dir = (self.vault_path / note_rel).parent
        inbox_dir.mkdir(parents=True, exist_ok=True)

        linked_topics = self._select_topics(summary)
        skipped_topics = [topic for topic in unique_topics(summary) if topic not in linked_topics]
        self._remove_note_from_all_topic_indexes(note_rel.as_posix())
        topic_links = self._update_topic_indexes(linked_topics, note_rel.as_posix())
        self._prune_empty_topic_indexes()
        keyword_l1, keyword_l2 = self._extract_keyword_hierarchy(summary, evidence)
        self._remove_note_from_all_keyword_indexes(note_rel.as_posix())
        keyword_links = self._update_keyword_indexes(keyword_l1, keyword_l2, note_rel.as_posix())
        entity_links = self._update_entity_pages(summary, note_rel.as_posix()) if self.config.auto_entity_pages else []
        structure_map = build_structure_map(summary, evidence, note_rel.as_posix(), topic_links, entity_links)
        content, note_render_error, materials_file = self._render_note_content(
            summary=summary,
            evidence=evidence,
            structure_map=structure_map,
            topic_links=topic_links,
            entity_links=entity_links,
            keyword_l1=keyword_l1,
            keyword_l2=keyword_l2,
            keyword_links=keyword_links,
            skipped_topics=skipped_topics,
            use_model_render=use_model_render,
        )
        payload = {
            "note_path": note_rel.as_posix(),
            "obsidian_uri": self._obsidian_uri(note_rel.as_posix()),
            "title": summary.title,
            "primary_topic": summary.primary_topic,
            "secondary_topics": ",".join(summary.secondary_topics),
            "topic_links": topic_links,
            "keyword_l1": keyword_l1 or "",
            "keyword_l2": ",".join(keyword_l2),
            "keyword_links": keyword_links,
            "entity_links": entity_links,
            "structure_map": structure_map,
        }
        if materials_file:
            payload["materials_file"] = materials_file
        if note_render_error:
            payload["note_render_error"] = note_render_error
            return payload
        assert content is not None
        path = self.vault_path / note_rel
        path.write_text(content, encoding="utf-8")
        return payload

    def preview(self, summary: SummaryResult, evidence: EvidenceBundle, use_model_render: bool = False) -> Dict[str, object]:
        note_rel = self._resolve_note_rel(summary, evidence)
        linked_topics = self._select_topics(summary)
        skipped_topics = [topic for topic in unique_topics(summary) if topic not in linked_topics]
        topic_links = self._build_topic_links(linked_topics)
        keyword_l1, keyword_l2 = self._extract_keyword_hierarchy(summary, evidence)
        keyword_links = self._build_keyword_links(keyword_l1, keyword_l2)
        entity_links = self._build_entity_links(summary) if self.config.auto_entity_pages else []
        structure_map = build_structure_map(summary, evidence, note_rel.as_posix(), topic_links, entity_links)
        content, note_render_error, materials_file = self._render_note_content(
            summary=summary,
            evidence=evidence,
            structure_map=structure_map,
            topic_links=topic_links,
            entity_links=entity_links,
            keyword_l1=keyword_l1,
            keyword_l2=keyword_l2,
            keyword_links=keyword_links,
            skipped_topics=skipped_topics,
            use_model_render=use_model_render,
        )
        payload = {
            "note_path": note_rel.as_posix(),
            "obsidian_uri": self._obsidian_uri(note_rel.as_posix()),
            "title": summary.title,
            "primary_topic": summary.primary_topic,
            "secondary_topics": ",".join(summary.secondary_topics),
            "topic_links": topic_links,
            "keyword_l1": keyword_l1 or "",
            "keyword_l2": ",".join(keyword_l2),
            "keyword_links": keyword_links,
            "entity_links": entity_links,
            "structure_map": structure_map,
        }
        if content is not None:
            payload["content"] = content
        if materials_file:
            payload["materials_file"] = materials_file
        if note_render_error:
            payload["note_render_error"] = note_render_error
        return payload

    def _resolve_note_rel(self, summary: SummaryResult, evidence: EvidenceBundle) -> Path:
        now = datetime.now()
        inbox_rel = Path(self.config.inbox_root) / now.strftime("%Y") / now.strftime("%m")
        existing_note_rel = self._find_existing_note_for_source(evidence.source_url)
        if existing_note_rel is not None:
            return existing_note_rel
        note_name = safe_name(f"{now.strftime('%Y-%m-%d %H%M')} {summary.title}") + ".md"
        return inbox_rel / note_name

    def _build_topic_links(self, topics: List[str]) -> List[str]:
        links: List[str] = []
        for topic in topics:
            topic_name = safe_name(topic)
            topic_rel = Path(self.config.topics_root) / topic_name / f"{topic_name} Index.md"
            links.append(f"[[{topic_rel.as_posix()}]]")
        return links

    def _build_entity_links(self, summary: SummaryResult) -> List[str]:
        links: List[str] = []
        for entity in summary.entities:
            entity_name = safe_name(entity)
            entity_rel = Path(self.config.entities_root) / f"{entity_name}.md"
            links.append(f"[[{entity_rel.as_posix()}]]")
        return links

    def _render_note_content(
        self,
        *,
        summary: SummaryResult,
        evidence: EvidenceBundle,
        structure_map: str,
        topic_links: List[str],
        entity_links: List[str],
        keyword_l1: str | None,
        keyword_l2: List[str],
        keyword_links: List[str],
        skipped_topics: List[str],
        use_model_render: bool = False,
    ) -> tuple[str | None, str | None, str]:
        canonical_source_url = self._canonical_source_url(evidence.source_url)
        frontmatter = [
            "---",
            f"title: {summary.title}",
            f"source_kind: {evidence.source_kind}",
            f"platform: {evidence.platform_hint or ''}",
            f"captured_at: {datetime.now().isoformat(timespec='seconds')}",
            f"source_url: {canonical_source_url or ''}",
            f"content_profile: {evidence.metadata.get('content_profile', {}).get('kind', '') if isinstance(evidence.metadata, dict) else ''}",
            f"keyword_l1: {keyword_l1 or ''}",
            f"keyword_l2: {','.join(keyword_l2)}",
            "---",
            "",
        ]
        materials = build_note_materials(
            summary=summary,
            evidence=evidence,
            structure_map=structure_map,
            topic_links=topic_links,
            entity_links=entity_links,
            keyword_links=keyword_links,
            skipped_topics=skipped_topics,
            canonical_source_url=canonical_source_url,
        )
        materials_file = save_materials_file(
            materials,
            self.materials_root,
            summary.title,
        )
        if self.renderer is None:
            return None, "note renderer unavailable", materials_file
        try:
            body = self.renderer.render(materials)
        except Exception as exc:
            return None, str(exc), materials_file
        if not body or not str(body).strip():
            return None, "note renderer returned empty content", materials_file
        polished_body = self._polish_rendered_body(str(body), summary, evidence, materials)
        content = "\n".join(frontmatter) + polished_body.rstrip() + "\n"
        return content, None, materials_file

    def _polish_rendered_body(
        self,
        body: str,
        summary: SummaryResult,
        evidence: EvidenceBundle,
        materials: Dict[str, object],
    ) -> str:
        text = str(body or "").strip()
        if not text:
            return text
        text = self._strip_debug_leaks(text)
        capture_status = {}
        context = materials.get("context", {}) if isinstance(materials, dict) else {}
        if isinstance(context, dict):
            capture_status = context.get("capture_status", {}) if isinstance(context.get("capture_status"), dict) else {}
        if capture_status.get("kind") == "video_extract_blocked":
            return self._build_blocked_video_body(summary, evidence, capture_status)
        text = re.sub(
            r"(?m)^(#{2,4})\s*可直接做的下一步\s*$",
            r"\1 贾维斯的思考",
            text,
        )
        if not self._should_keep_thought_checklist(summary, evidence):
            text = self._rewrite_thought_section_as_paragraph(text, evidence)
        return text

    def _strip_debug_leaks(self, text: str) -> str:
        blocked_tokens = [
            "python 3.9",
            "yt-dlp",
            "unsupported url",
            "traceback",
            "systemexit(",
            "deprecated feature",
        ]
        cleaned_lines: List[str] = []
        for line in text.splitlines():
            lowered = line.lower()
            if any(token in lowered for token in blocked_tokens):
                continue
            cleaned_lines.append(line.rstrip())
        return "\n".join(cleaned_lines).strip()

    def _build_blocked_video_body(
        self,
        summary: SummaryResult,
        evidence: EvidenceBundle,
        capture_status: Dict[str, object],
    ) -> str:
        title = summary.title or evidence.title or "这条内容"
        summary_line = re.sub(r"\s+", " ", str(capture_status.get("summary", "")).strip())
        if not summary_line:
            summary_line = "这条内容当前没拿到有效正文，继续看这版结果意义不大。"
        paragraphs = [
            f"# {title}",
            "",
            f"{summary_line} 现在只能确认它来自平台侧受限或分享链路不可直接读取，而不是内容本身没有价值。",
            "",
            "如果之后能拿到正常页面、音频或画面证据，再回来看会更有意义；以目前这版结果，先别在这条上继续花时间。",
            "",
            "## 贾维斯的思考",
            "",
            "如果我是你，这条我会先放到一边，等链接能正常打开或者能拿到可读内容后再判断值不值得深挖。",
        ]
        return "\n".join(paragraphs).strip()

    def _should_keep_thought_checklist(self, summary: SummaryResult, evidence: EvidenceBundle) -> bool:
        metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
        profile = metadata.get("content_profile", {}) if isinstance(metadata.get("content_profile"), dict) else {}
        kind = str(profile.get("kind", "")).strip()
        if kind in {"installation_tutorial", "skill_recommendation"}:
            return True
        actions = [str(item).strip() for item in summary.follow_up_actions if str(item).strip()]
        action_corpus = "\n".join(actions).lower()
        return any(token in action_corpus for token in ["/install-skill", "执行命令", "验证", "安装", "部署", "运行"])

    def _rewrite_thought_section_as_paragraph(self, text: str, evidence: EvidenceBundle) -> str:
        lines = text.splitlines()
        result: List[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            heading_match = re.match(r"^(#{2,4})\s*(贾维斯的思考)\s*$", line.strip())
            if not heading_match:
                result.append(line)
                i += 1
                continue
            level = heading_match.group(1)
            result.append(f"{level} 贾维斯的思考")
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            section_lines: List[str] = []
            while i < len(lines) and not re.match(r"^#{1,6}\s+", lines[i].strip()):
                section_lines.append(lines[i])
                i += 1
            actions: List[str] = []
            for raw in section_lines:
                stripped = raw.strip()
                if not stripped:
                    continue
                bullet_match = re.match(r"^(?:[-*]|\d+\.)\s+(.+)$", stripped)
                if bullet_match:
                    actions.append(bullet_match.group(1).strip().rstrip("。；;"))
                else:
                    actions.append(stripped.rstrip("。；;"))
            actions = [item for item in actions if item]
            if actions:
                result.append("")
                result.append(self._jarvis_thought_paragraph(actions, evidence))
            continue
        return "\n".join(result).strip()

    def _jarvis_thought_paragraph(self, actions: List[str], evidence: EvidenceBundle) -> str:
        picked = [self._normalize_jarvis_action(item) for item in actions[:3] if self._normalize_jarvis_action(item)]
        if not picked:
            return "如果我是你，我会先把这条内容留在待看清单里，等真正需要的时候再回来看。"
        if len(picked) == 1:
            if picked[0].startswith("如果我是你"):
                return picked[0].rstrip("。；;") + "。"
            return f"如果我是你，我会先{picked[0]}，然后再决定要不要继续往下投入。"
        if len(picked) == 2:
            return f"如果我是你，我会先{picked[0]}，再{picked[1]}，这样基本就能判断这条内容值不值得继续跟。"
        return f"如果我是你，我会先{picked[0]}，再{picked[1]}，最后{picked[2]}；走到这一步，通常就知道这条内容该继续深挖还是先放着。"

    def _normalize_jarvis_action(self, value: str) -> str:
        text = re.sub(r"\s+", " ", str(value).strip()).strip("。；;")
        if not text:
            return ""
        text = re.sub(r"^如果我是你[，,]?(?:这条我)?会先", "", text).strip(" ，,")
        text = re.sub(r"^我会先", "", text).strip(" ，,")
        return text

    def _keyword_root_rel(self) -> Path:
        return Path(self.config.topics_root) / "_Keywords"

    def _keyword_root_path(self) -> Path:
        return self.vault_path / self._keyword_root_rel()

    def _build_keyword_links(self, keyword_l1: str | None, keyword_l2: List[str]) -> List[str]:
        if not keyword_l1:
            return []
        links: List[str] = []
        primary_name = safe_name(keyword_l1)
        primary_rel = self._keyword_root_rel() / primary_name / f"{primary_name} Index.md"
        links.append(f"[[{primary_rel.as_posix()}]]")
        for keyword in keyword_l2:
            secondary_name = safe_name(keyword)
            secondary_rel = self._keyword_root_rel() / primary_name / f"{secondary_name}.md"
            links.append(f"[[{secondary_rel.as_posix()}]]")
        return links

    def _update_keyword_indexes(self, keyword_l1: str | None, keyword_l2: List[str], note_rel_path: str) -> List[str]:
        if not keyword_l1:
            return []
        keyword_root = self._keyword_root_path()
        primary_name = safe_name(keyword_l1)
        primary_dir = keyword_root / primary_name
        primary_dir.mkdir(parents=True, exist_ok=True)
        primary_rel = self._keyword_root_rel() / primary_name / f"{primary_name} Index.md"
        primary_path = self.vault_path / primary_rel
        primary_text = primary_path.read_text(encoding="utf-8") if primary_path.exists() else f"# {keyword_l1}\n\n## 二级关键词\n\n## 相关笔记\n"
        note_entry = f"- [[{note_rel_path}]]"
        if note_entry not in primary_text:
            primary_text = primary_text.rstrip() + "\n" + note_entry + "\n"
        for keyword in keyword_l2:
            secondary_name = safe_name(keyword)
            secondary_rel = self._keyword_root_rel() / primary_name / f"{secondary_name}.md"
            secondary_entry = f"- [[{secondary_rel.as_posix()}]]"
            if secondary_entry not in primary_text:
                primary_text = primary_text.rstrip() + "\n" + secondary_entry + "\n"
            secondary_path = self.vault_path / secondary_rel
            secondary_path.parent.mkdir(parents=True, exist_ok=True)
            secondary_text = (
                secondary_path.read_text(encoding="utf-8")
                if secondary_path.exists()
                else f"# {keyword}\n\n上级关键词: [[{primary_rel.as_posix()}]]\n\n## 相关笔记\n"
            )
            if note_entry not in secondary_text:
                secondary_text = secondary_text.rstrip() + "\n" + note_entry + "\n"
            secondary_path.write_text(secondary_text, encoding="utf-8")
        primary_path.write_text(primary_text, encoding="utf-8")
        return self._build_keyword_links(keyword_l1, keyword_l2)

    def _remove_note_from_all_keyword_indexes(self, note_rel_path: str) -> None:
        keyword_root = self._keyword_root_path()
        if not keyword_root.exists():
            return
        entry = f"- [[{note_rel_path}]]"
        for keyword_path in keyword_root.rglob("*.md"):
            existing = keyword_path.read_text(encoding="utf-8")
            if entry not in existing:
                continue
            lines = [line for line in existing.splitlines() if line.strip() != entry]
            keyword_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def _extract_keyword_hierarchy(self, summary: SummaryResult, evidence: EvidenceBundle) -> tuple[str | None, List[str]]:
        metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
        signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
        generic = {"未分类", "视频", "图文", "内容", "链接", "网页", "文章"}

        def normalize(value: str) -> str:
            return re.sub(r"\s+", " ", str(value).strip()).strip("#[]()")

        def reasonable(value: str) -> bool:
            text = normalize(value)
            if not text or text in generic:
                return False
            if len(text) < 2 or len(text) > 36:
                return False
            if text.startswith(("http://", "https://")):
                return False
            return True

        corpus_parts = [
            summary.title,
            summary.primary_topic,
            *summary.secondary_topics,
            *summary.entities,
            *summary.note_tags,
            summary.conclusion,
            *summary.bullets,
            evidence.title or "",
            evidence.text or "",
        ]
        if isinstance(signals, dict):
            for key in ["skills", "skill_ids", "projects", "hashtags"]:
                values = signals.get(key, [])
                if isinstance(values, list):
                    corpus_parts.extend([str(item) for item in values[:6]])
        bilibili_tags = metadata.get("bilibili_tags", []) if isinstance(metadata.get("bilibili_tags"), list) else []
        corpus_parts.extend([str(item) for item in bilibili_tags[:8]])
        corpus = "\n".join([normalize(item) for item in corpus_parts if normalize(item)]).lower()

        def token_hit(token: str) -> bool:
            lowered = token.lower()
            if re.fullmatch(r"[a-z0-9.+_-]+", lowered) and len(lowered) <= 4:
                return bool(re.search(rf"\b{re.escape(lowered)}\b", corpus))
            return lowered in corpus

        category_rules = [
            ("网络安全", ["0day", "漏洞", "二进制", "逆向", "摄像头", "iot", "安全", "exploit", "kali", "ida"]),
            ("游戏", ["杀戮尖塔", "游戏", "卡牌", "流派", "攻略", "boss", "肉鸽", "steam"]),
            ("股票投资", ["股票", "财报", "量化", "交易", "自选股", "美股", "投资", "大盘", "行情"]),
            ("出海SaaS", ["saas", "出海", "支付", "海外公司", "订阅", "billing"]),
            ("求职职场", ["薪资", "求职", "面试", "背调", "offer", "职场", "工资"]),
            ("知识管理", ["obsidian", "知识管理", "笔记", "pkm", "claudian"]),
            ("饮食健康", ["热量", "减脂", "饮食", "外出就餐", "大卡", "包子", "鸡蛋粥"]),
            ("人物访谈", ["访谈", "方法论", "人生", "美国梦", "老兵", "故事", "对话"]),
            ("AI工具", ["openclaw", "claude", "gpt", "ai", "agent", "skill", "rag", "模型"]),
            ("编程开发", ["python", "kubernetes", "container runtime", "nodejs", "开发", "编程", "runtime", "docker"]),
            ("开源项目", ["github", "开源", "repo", "仓库", "project"]),
            ("效率工具", ["效率", "工作流", "自动化", "生产力", "工具"]),
        ]
        keyword_l1 = None
        for label, tokens in category_rules:
            if any(token_hit(token) for token in tokens):
                keyword_l1 = label
                break
        if keyword_l1 is None:
            primary_candidates = [
                summary.primary_topic,
                *summary.secondary_topics,
                *summary.entities,
            ]
            if isinstance(signals, dict):
                primary_candidates.extend([str(item) for item in signals.get("skills", [])[:2]])
                primary_candidates.extend([str(item) for item in signals.get("projects", [])[:1]])
            keyword_l1 = next((normalize(item) for item in primary_candidates if reasonable(item)), None)

        secondary_candidates: List[str] = []
        secondary_candidates.extend([normalize(item) for item in summary.secondary_topics if reasonable(item)])
        secondary_candidates.extend([normalize(item) for item in summary.entities if reasonable(item)])
        secondary_candidates.extend([normalize(item) for item in summary.note_tags if reasonable(item)])
        secondary_candidates.extend([normalize(str(item)) for item in bilibili_tags if reasonable(str(item))])
        if isinstance(signals, dict):
            secondary_candidates.extend([normalize(str(item)) for item in signals.get("skills", [])[:3] if reasonable(str(item))])
            secondary_candidates.extend([normalize(str(item)) for item in signals.get("skill_ids", [])[:3] if reasonable(str(item))])
            secondary_candidates.extend([normalize(str(item)) for item in signals.get("projects", [])[:2] if reasonable(str(item))])
        title = normalize(evidence.title or summary.title)
        title_patterns = [
            r"(杀戮尖塔\d?)",
            r"(OpenClaw)",
            r"(Claudian)",
            r"(Trading Agents)",
            r"(OpenRAG)",
            r"(DCS\d+[A-Z]?)",
            r"(0day)",
            r"(Obsidian)",
        ]
        for pattern in title_patterns:
            match = re.search(pattern, title, flags=re.IGNORECASE)
            if match:
                secondary_candidates.append(normalize(match.group(1)))

        keyword_l2: List[str] = []
        for item in secondary_candidates:
            if not item or item == keyword_l1 or item in keyword_l2:
                continue
            keyword_l2.append(item)
            if len(keyword_l2) >= 4:
                break
        return keyword_l1, keyword_l2

    def _build_related_links(self, topic_links: List[str], entity_links: List[str]) -> List[str]:
        links: List[str] = []
        for item in topic_links[:3]:
            if item not in links:
                links.append("主题: " + item)
        for item in entity_links[:4]:
            if item not in links:
                links.append("实体: " + item)
        return links

    def _build_keyword_badges(self, summary: SummaryResult, signals: Dict[str, object]) -> str:
        items: List[str] = []
        items.append(summary.primary_topic)
        items.extend(summary.secondary_topics[:3])
        if isinstance(signals, dict):
            items.extend([str(item) for item in signals.get("projects", [])[:2]])
            items.extend([str(item) for item in signals.get("skills", [])[:2]])
            items.extend([str(item) for item in signals.get("skill_ids", [])[:3]])
        items.extend(summary.entities[:4])
        deduped: List[str] = []
        seen: set[str] = set()
        for raw in items:
            text = re.sub(r"\s+", " ", str(raw).strip())
            if not text or len(text) > 40:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(f"**{text}**")
            if len(deduped) >= 8:
                break
        return " ".join(deduped)

    def _build_usefulness_lines(self, summary: SummaryResult, evidence: EvidenceBundle, signals: Dict[str, object]) -> List[str]:
        profile = evidence.metadata.get("content_profile", {}) if isinstance(evidence.metadata, dict) else {}
        kind = str(profile.get("kind", "general_capture"))
        coverage = summary.coverage
        lines: List[str] = []
        if kind == "skill_recommendation":
            lines.append("如果只是想判断这个 Skill 要不要收、要不要装，这页已经够了。")
            lines.append("入口、Skill ID 和安装动作都已经拎出来了。")
        elif kind == "installation_tutorial":
            lines.append("如果打算之后照着做，这页已经把最容易漏掉的前置条件和动作拎出来了。")
            lines.append("不用再回原文翻命令。")
        elif kind == "project_overview":
            lines.append("如果只是想判断这个项目值不值得继续跟，这页基本已经够用。")
            lines.append("是该收藏、试跑，还是只留档，通常能先做个决定。")
        elif kind == "video_explainer":
            guidance = ""
            if isinstance(evidence.metadata, dict):
                guidance = re.sub(r"\s+", " ", str(evidence.metadata.get("user_guidance", "")).strip())
            if any(token in guidance for token in ["学项目", "项目有没有帮助", "学这个项目"]):
                lines.append("如果你现在只想判断这个项目值不值得学，这页已经够你先做决定。")
            elif coverage == "partial":
                lines.append("这版信息只够先做初筛，还不适合直接拍板。")
            else:
                lines.append("如果你只想先判断有没有继续看的价值，这页基本已经够了。")
        else:
            lines.append("先看这页，通常就能判断这条内容是该继续跟，还是先留档。")
        return lines[:2]

    def _build_secretary_judgment_lines(self, summary: SummaryResult) -> List[str]:
        timing_map = {"high": "高", "medium": "中", "low": "低"}
        effectiveness_map = {"high": "高", "medium": "中", "low": "低"}
        recommendation_map = {
            "must_read": "强烈推荐",
            "recommended": "建议看",
            "optional": "按需看",
            "skip": "可跳过",
        }
        lines = [
            "适用身份: 大厂程序员",
            f"时效性: {timing_map.get(summary.timeliness, '中')}",
            f"有效程度: {effectiveness_map.get(summary.effectiveness, '中')}",
            f"推荐等级: {recommendation_map.get(summary.recommendation_level, '按需看')}",
        ]
        judgment = re.sub(r"\s+", " ", str(summary.reader_judgment or "").strip())
        if judgment:
            lines.append("判断: " + judgment)
        return lines

    def _build_text_mind_map(
        self,
        *,
        title: str,
        conclusion: str,
        summary: SummaryResult,
        evidence: EvidenceBundle,
        project_lines: List[str],
        action_items: List[str],
        related_links: List[str],
    ) -> str:
        profile = evidence.metadata.get("content_profile", {}) if isinstance(evidence.metadata, dict) else {}
        kind = str(profile.get("kind", "general_capture"))
        focus = self._pick_mind_map_focus(summary, project_lines)
        action = self._pick_mind_map_action(evidence, action_items)
        related = related_links[0] if related_links else "无"
        lines = [
            f"主题: {title}",
            f"├─ 结论: {conclusion}",
            f"├─ 类型: {kind}",
            f"├─ 核心对象: {focus}",
            f"├─ 下一步: {action}",
            f"└─ 关联: {related}",
        ]
        return "\n".join(lines)

    def _pick_mind_map_focus(self, summary: SummaryResult, project_lines: List[str]) -> str:
        for raw in project_lines:
            line = raw.replace("- ", "", 1)
            if line.startswith(("项目名称:", "技能名:", "技能ID:", "安装方法:", "关键命令:", "使用方式:", "核心用途:")):
                return line
        for bullet in self._dedupe_core_bullets(summary.bullets):
            candidate = self._mind_map_fact_text(bullet)
            if candidate:
                return candidate
        return summary.primary_topic

    def _pick_mind_map_action(self, evidence: EvidenceBundle, action_items: List[str]) -> str:
        if action_items:
            return action_items[0]
        if evidence.source_kind == "video_url":
            return "先看上面的关键事实再决定要不要回看原视频"
        return "先看上面的关键事实再决定要不要投入时间"

    def _mind_map_fact_text(self, bullet: str) -> str:
        text = re.sub(r"\s+", " ", str(bullet).strip()).strip("。；;")
        if not text:
            return ""
        if ":" in text:
            label, rest = text.split(":", 1)
            if label.strip() in {"项目名称", "GitHub地址", "视频链接", "关键链接", "技能名", "技能ID", "安装方法", "关键命令"}:
                text = rest.strip()
        elif "：" in text:
            label, rest = text.split("：", 1)
            if label.strip() in {"项目名称", "GitHub地址", "视频链接", "关键链接", "技能名", "技能ID", "安装方法", "关键命令"}:
                text = rest.strip()
        if not text or text.startswith(("http://", "https://")):
            return ""
        if len(text) > 60:
            text = text[:60].rstrip() + "..."
        return text

    def _compact_evidence_lines(self, text: str, source_url: str | None = None) -> List[str]:
        value = (text or "").strip()
        if not value:
            return []
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if not lines:
            return []
        unique_lines: list[str] = []
        seen_normalized: set[str] = set()
        for line in lines:
            normalized = re.sub(r"\s+", " ", line).strip().lower()
            if not normalized or normalized in seen_normalized:
                continue
            seen_normalized.add(normalized)
            unique_lines.append(line)
        candidates: list[tuple[int, str]] = []
        for line in unique_lines:
            lowered = line.lower()
            lowered_compact = lowered.replace(" ", "")
            if line == (source_url or "").strip():
                continue
            if re.fullmatch(r"https?://\S+", line):
                continue
            if line.startswith(("编辑于", "发布于", "更新于")):
                continue
            if "skills就是claude提出来" in lowered_compact:
                continue
            if "token 消耗" in line or "token消耗" in lowered_compact:
                continue
            if line.startswith("[") and line.endswith("]"):
                continue
            if len(line) < 8:
                continue
            if any(token in lowered for token in ["notifications", "issues", "pull requests", "license", "watchers"]):
                continue

            score = 0
            if line.startswith(
                (
                    "项目仓库:",
                    "项目名称:",
                    "仓库地址:",
                    "GitHub地址:",
                    "技能名:",
                    "技能ID:",
                    "命令:",
                    "关键命令:",
                    "安装方法:",
                    "前置条件:",
                    "验证动作:",
                    "使用方式:",
                    "核心用途:",
                )
            ):
                score += 8
            if "github.com/" in lowered or line.startswith(("http://", "https://")):
                score += 7
            if "/install-skill" in lowered or line.startswith("命令："):
                score += 7
            if re.search(r"\b[a-z][a-z0-9]+(?:-[a-z0-9]+){1,5}\b", line):
                score += 4
            if any(token in line for token in ["安装", "使用", "用法", "自动激活", "读取", "输出", "决策", "分析"]):
                score += 3
            if score <= 0 and len(line) < 20:
                continue
            candidates.append((score, line))

        deduped: list[str] = []
        seen: set[str] = set()
        for _, line in sorted(candidates, key=lambda item: item[0], reverse=True):
            normalized_line = re.sub(r"[.。…]+$", "", line).strip().lower()
            if normalized_line in seen:
                continue
            seen.add(normalized_line)
            if len(line) > 140:
                line = line[:140].rstrip() + "..."
            deduped.append(line)
            if len(deduped) >= 4:
                break
        if deduped:
            return deduped

        # Fallback: keep first meaningful evidence lines for subtitle-heavy videos.
        fallback: list[str] = []
        for line in unique_lines:
            normalized = re.sub(r"\s+", " ", line).strip()
            if len(normalized) < 12 or len(normalized) > 140:
                continue
            if normalized.startswith("[") and normalized.endswith("]"):
                continue
            if re.fullmatch(r"https?://\S+", normalized):
                continue
            if any(token in normalized for token in ["相关推荐", "点赞", "收藏", "评论", "登录", "注册"]):
                continue
            fallback.append(normalized)
            if len(fallback) >= 3:
                break
        if fallback:
            return fallback
        return deduped

    def _dedupe_core_bullets(self, bullets: List[str]) -> List[str]:
        category_prefix = (
            "项目名称:",
            "GitHub地址:",
            "视频链接:",
            "关键链接:",
            "项目仓库:",
            "仓库地址:",
            "项目:",
            "链接:",
            "技能名:",
            "技能ID:",
            "安装方法:",
            "关键命令:",
            "前置条件:",
            "验证动作:",
            "使用方式:",
            "核心用途:",
        )
        result: List[str] = []
        seen_labels: set[str] = set()
        for raw in bullets:
            text = re.sub(r"\s+", " ", str(raw).strip()).strip("。；;")
            if not text:
                continue
            if text in {"已提取核心事实", "已提取核心事实。"}:
                continue
            if any(
                token in text
                for token in [
                    "这条内容主要",
                    "对你最直接的价值",
                    "你可以先看",
                    "对你最有用的是",
                    "你不用从长文",
                ]
            ):
                continue
            if ":" in text:
                label = text.split(":", 1)[0].strip()
                if label in seen_labels:
                    continue
                seen_labels.add(label)
            elif "：" in text:
                label = text.split("：", 1)[0].strip()
                if label in seen_labels:
                    continue
                seen_labels.add(label)
            if text in result:
                continue
            result.append(text)
            if len(result) >= 6:
                break
        filtered = [item for item in result if not item.startswith(category_prefix)]
        if filtered:
            return filtered[:6]
        return result[:6]

    def _build_explainer_paragraph(self, summary: SummaryResult, signals: Dict[str, object], source_kind: str) -> str:
        skills = [str(item) for item in signals.get("skills", [])] if isinstance(signals, dict) else []
        projects = [str(item) for item in signals.get("projects", [])] if isinstance(signals, dict) else []
        skill_ids = [str(item) for item in signals.get("skill_ids", [])] if isinstance(signals, dict) else []
        links = [str(item) for item in signals.get("links", [])] if isinstance(signals, dict) else []
        skill_name = skills[0] if skills else ""
        project = projects[0] if projects else ""
        bullets = self._dedupe_core_bullets(summary.bullets)
        fact_points: List[str] = []
        for item in bullets:
            raw_cleaned = item.strip()
            if re.fullmatch(r"\d{1,2}:\d{2}\s*/\s*\d{1,2}:\d{2}", raw_cleaned):
                continue
            cleaned = item
            if ":" in cleaned:
                cleaned = cleaned.split(":", 1)[1].strip()
            elif "：" in cleaned:
                cleaned = cleaned.split("：", 1)[1].strip()
            lowered = cleaned.lower()
            if not cleaned:
                continue
            if "|" in cleaned:
                cleaned = cleaned.split("|", 1)[0].strip()
            if cleaned.startswith("[") and cleaned.endswith("]"):
                continue
            if cleaned.startswith(("http://", "https://")):
                continue
            if "github.com/" in lowered:
                continue
            if re.fullmatch(r"\d{1,2}:\d{2}\s*/\s*\d{1,2}:\d{2}", cleaned):
                continue
            if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", cleaned):
                continue
            if cleaned.startswith(("推荐一个", "这条内容", "对你最有用")):
                continue
            if len(cleaned) > 72:
                continue
            if cleaned not in fact_points:
                fact_points.append(cleaned)
            if len(fact_points) >= 2:
                break
        if skill_name and not any(skill_name in point for point in fact_points):
            fact_points.insert(0, f"技能名称为 {skill_name}")
        if skill_ids and not any(skill_ids[0] in point for point in fact_points):
            fact_points.append(f"核心技能ID为 {skill_ids[0]}")
        if project and not any(project in point for point in fact_points):
            fact_points.append(f"项目仓库为 {project}")
        has_github_link = any("github.com/" in link.lower() for link in links)
        if has_github_link and not any("GitHub" in point for point in fact_points):
            fact_points.append("支持通过 GitHub 链接安装与追溯")
        if source_kind == "video_url" and len(fact_points) < 2:
            fact_points.append("抽取视频主线与关键结论")
        if source_kind == "image" and len(fact_points) < 2:
            fact_points.append("提取图片中的关键信息")
        while len(fact_points) < 2:
            fact_points.append("当前证据以显式可见文本为准")
        if source_kind == "video_url":
            text = f"如果只想先判断值不值得回看，这条视频最核心的是{fact_points[0]}；另外它还补充了{fact_points[1]}。"
            if summary.coverage == "partial":
                text += "当前更适合先放进待筛选清单。"
            else:
                text += "整体看，值得留在待看清单里。"
        elif source_kind == "image":
            text = f"这张图最值得记住的是{fact_points[0]}；另外也说明了{fact_points[1]}。"
            text += "适合用于把截图信息转成结构化知识，便于后续追踪与比对。"
        else:
            if project and skill_name:
                intro = f"该条目聚焦 {skill_name}，对应仓库为 {project}。"
            elif project:
                intro = f"该条目围绕仓库 {project} 的能力与用法展开。"
            else:
                topic = summary.primary_topic if summary.primary_topic and summary.primary_topic != "未分类" else summary.title
                intro = f"该条目核心主题为 {topic}。"
            text = intro + f"现在最该记住的两点是：{fact_points[0]}；{fact_points[1]}。"
            text += "适合直接沉淀为知识卡片。"
        if summary.coverage == "partial" and source_kind != "video_url":
            text += "当前证据覆盖不完整，建议补充原文或更多上下文后再下最终判断。"
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 55:
            text += "可结合上方关键要点与证据摘录进行二次验证。"
        if len(text) > 140:
            text = text[:140].rstrip("，。；; ") + "。"
        return text

    def _build_action_items(self, actions: List[str]) -> List[str]:
        result: List[str] = []
        for raw in actions:
            item = re.sub(r"\s+", " ", str(raw).strip()).strip("。；;")
            if not item:
                continue
            if item in result:
                continue
            if len(item) > 100:
                item = item[:100].rstrip() + "..."
            result.append(item)
            if len(result) >= 6:
                break
        return result

    def _clip_signal_line(self, value: str, limit: int) -> str:
        text = re.sub(r"\s+", " ", value.strip())
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    def _build_video_reliability_lines(self, evidence: EvidenceBundle) -> List[str]:
        if evidence.source_kind != "video_url":
            return []
        metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
        tracks = metadata.get("tracks", {}) if isinstance(metadata.get("tracks"), dict) else {}
        sources = metadata.get("evidence_sources", []) if isinstance(metadata.get("evidence_sources"), list) else []
        reasons = metadata.get("video_gate_reasons", []) if isinstance(metadata.get("video_gate_reasons"), list) else []
        lines: List[str] = []
        if sources:
            lines.append("证据来源: " + " | ".join([str(item) for item in sources[:6]]))
        track_parts: List[str] = []
        for label, key in [
            ("字幕", "has_subtitle"),
            ("转写", "has_transcript"),
            ("关键帧", "has_keyframes"),
            ("关键帧OCR", "has_keyframe_ocr"),
        ]:
            track_parts.append(f"{label}={'有' if tracks.get(key) else '无'}")
        if track_parts:
            lines.append("轨道状态: " + " | ".join(track_parts))
        if reasons:
            lines.append("当前局限: " + "；".join([str(item) for item in reasons[:3]]))
            lines.append("建议动作: 优先补抓字幕或语音轨后再下最终判断")
        else:
            lines.append("当前局限: 未发现明显证据缺口，可直接人工复核")
        return lines

    def _collect_focus_terms(self, summary: SummaryResult, signals: Dict[str, object]) -> List[str]:
        terms: List[str] = []
        if isinstance(signals, dict):
            terms.extend([str(item).strip() for item in signals.get("projects", [])[:2]])
            terms.extend([str(item).strip() for item in signals.get("skill_ids", [])[:3]])
            terms.extend([str(item).strip() for item in signals.get("skills", [])[:2]])
        terms.extend([str(item).strip() for item in summary.entities[:3]])
        terms.extend(["OpenClaw", "Telegram", "GitHub"])
        seen: set[str] = set()
        picked: List[str] = []
        for term in sorted(terms, key=len, reverse=True):
            if not term or len(term) < 3:
                continue
            if term.startswith("http://") or term.startswith("https://"):
                continue
            if "|" in term:
                continue
            key = term.lower()
            if key in seen:
                continue
            if any(key in existing.lower() for existing in picked):
                continue
            seen.add(key)
            picked.append(term)
            if len(picked) >= 12:
                break
        return picked

    def _emphasize_terms(self, text: str, terms: List[str]) -> str:
        value = text or ""
        if not value or not terms:
            return value
        valid_terms = [term for term in sorted(terms, key=len, reverse=True) if term and term in value]
        if not valid_terms:
            return value
        pattern = re.compile("|".join(re.escape(term) for term in valid_terms))
        placeholders: Dict[str, str] = {}

        def replacer(match: re.Match[str]) -> str:
            token = f"__OC_HL_{len(placeholders)}__"
            placeholders[token] = f"**{match.group(0)}**"
            return token

        rendered = pattern.sub(replacer, value)
        for token, marked in placeholders.items():
            rendered = rendered.replace(token, marked)
        return rendered

    def _canonical_source_url(self, source_url: str | None) -> str:
        if not source_url:
            return ""
        try:
            parsed = urlsplit(source_url.strip())
            if not parsed.scheme or not parsed.netloc:
                return source_url.strip()
            path = parsed.path or "/"
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
            return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, normalized_query, ""))
        except ValueError:
            return source_url.strip()

    def _find_existing_note_for_source(self, source_url: str | None) -> Path | None:
        target = self._canonical_source_url(source_url)
        if not target:
            return None
        inbox_root = self.vault_path / self.config.inbox_root
        if not inbox_root.exists():
            return None
        matches: List[Path] = []
        for path in inbox_root.rglob("*.md"):
            existing = self._canonical_source_url(self._read_source_url(path))
            if existing == target:
                matches.append(path)
        if not matches:
            return None
        latest = sorted(matches)[-1]
        return latest.relative_to(self.vault_path)

    def _read_source_url(self, path: Path) -> str | None:
        frontmatter_value = self._read_frontmatter_value(path, "source_url")
        if frontmatter_value:
            return frontmatter_value
        text = path.read_text(encoding="utf-8")
        comment_match = re.search(r"<!--\s*source_url:\s*(https?://\S+)\s*-->", text)
        if comment_match:
            return comment_match.group(1).strip()
        return None

    def _read_frontmatter_value(self, path: Path, key: str) -> str | None:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return None
        end = text.find("\n---\n", 4)
        if end == -1:
            return None
        frontmatter = text[4:end]
        pattern = re.compile(rf"^{re.escape(key)}:\s*(.*)$", re.MULTILINE)
        match = pattern.search(frontmatter)
        if not match:
            return None
        return match.group(1).strip()

    def _obsidian_uri(self, note_rel_path: str) -> str:
        vault = quote(self.vault_name, safe="")
        file_path = quote(note_rel_path, safe="/")
        return f"obsidian://open?vault={vault}&file={file_path}"

    def _update_topic_indexes(self, topics: List[str], note_rel_path: str) -> List[str]:
        links = []
        for topic in topics:
            topic_name = safe_name(topic)
            topic_rel = Path(self.config.topics_root) / topic_name / f"{topic_name} Index.md"
            topic_path = self.vault_path / topic_rel
            topic_path.parent.mkdir(parents=True, exist_ok=True)
            if topic_path.exists():
                existing = topic_path.read_text(encoding="utf-8")
            else:
                existing = f"# {topic_name}\n\n## 笔记\n"
            entry = f"- [[{note_rel_path}]]"
            if entry not in existing:
                existing = existing.rstrip() + "\n" + entry + "\n"
                topic_path.write_text(existing, encoding="utf-8")
            links.append(f"[[{topic_rel.as_posix()}]]")
        return links

    def _remove_note_from_all_topic_indexes(self, note_rel_path: str) -> None:
        topics_root = self.vault_path / self.config.topics_root
        if not topics_root.exists():
            return
        entry = f"- [[{note_rel_path}]]"
        for topic_path in topics_root.rglob("* Index.md"):
            existing = topic_path.read_text(encoding="utf-8")
            if entry not in existing:
                continue
            lines = [line for line in existing.splitlines() if line.strip() != entry]
            topic_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def _update_entity_pages(self, summary: SummaryResult, note_rel_path: str) -> List[str]:
        links = []
        for entity in summary.entities:
            entity_name = safe_name(entity)
            entity_rel = Path(self.config.entities_root) / f"{entity_name}.md"
            entity_path = self.vault_path / entity_rel
            entity_path.parent.mkdir(parents=True, exist_ok=True)
            if entity_path.exists():
                existing = entity_path.read_text(encoding="utf-8")
            else:
                existing = f"# {entity_name}\n\n## 相关笔记\n"
            entry = f"- [[{note_rel_path}]]"
            if entry not in existing:
                existing = existing.rstrip() + "\n" + entry + "\n"
                entity_path.write_text(existing, encoding="utf-8")
            links.append(f"[[{entity_rel.as_posix()}]]")
        return links

    def _select_topics(self, summary: SummaryResult) -> List[str]:
        topics = unique_topics(summary)
        if not topics:
            return []
        selected: List[str] = []
        primary = topics[0]
        for topic in topics:
            if not self._is_reasonable_topic(topic):
                continue
            if topic in self.topic_whitelist:
                selected.append(topic)
            elif topic == primary and self._topic_note_count(topic) >= 1:
                selected.append(topic)
            elif topic != primary and self._is_reasonable_secondary(topic) and self._topic_note_count(topic) >= 2:
                selected.append(topic)
        return selected[:3]

    def _is_reasonable_topic(self, topic: str) -> bool:
        topic = topic.strip()
        if not topic:
            return False
        if len(topic) < 2 or len(topic) > 16:
            return False
        lowered = topic.lower()
        for token in self.topic_blocklist:
            if token.lower() in lowered:
                return False
        if re.search(r"[0-9]{3,}", topic):
            return False
        if any(ch in topic for ch in ["/", "\\", "[", "]", "《", "》", "(", ")", ":"]):
            return False
        return True

    def _is_reasonable_secondary(self, topic: str) -> bool:
        return self._is_reasonable_topic(topic) and len(topic) <= 10

    def _topic_note_count(self, topic: str) -> int:
        topic_name = safe_name(topic)
        topic_rel = Path(self.config.topics_root) / topic_name / f"{topic_name} Index.md"
        topic_path = self.vault_path / topic_rel
        if not topic_path.exists():
            return 0
        count = 0
        for line in topic_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("- [[") and line.strip().endswith("]]"):
                count += 1
        return count

    def _prune_empty_topic_indexes(self) -> None:
        topics_root = self.vault_path / self.config.topics_root
        if not topics_root.exists():
            return
        try:
            topic_paths = list(topics_root.rglob("* Index.md"))
        except FileNotFoundError:
            return
        for topic_path in topic_paths:
            if "_Keywords" in topic_path.parts:
                continue
            if not topic_path.exists():
                continue
            lines = topic_path.read_text(encoding="utf-8").splitlines()
            has_note_link = any(line.strip().startswith("- [[") and line.strip().endswith("]]") for line in lines)
            if has_note_link:
                continue
            try:
                topic_path.unlink()
            except OSError:
                continue
            topic_dir = topic_path.parent
            try:
                next(topic_dir.iterdir())
            except (StopIteration, FileNotFoundError):
                topic_dir.rmdir()
