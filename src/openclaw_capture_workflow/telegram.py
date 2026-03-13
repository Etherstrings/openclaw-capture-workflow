"""Telegram result sender."""

from __future__ import annotations

import json
import re
from urllib import parse as urlparse
from urllib import request as urlrequest

from .models import IngestRequest, SummaryResult


def _sanitize_for_telegram(text: str) -> str:
    text = text.replace("[[", "《").replace("]]", "》")
    text = re.sub(r"\.md\b", " [md]", text)
    return text


def _truncate_for_telegram(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)] + "...(truncated)"


def _compact_bullets(summary: SummaryResult, limit: int = 4) -> list[str]:
    items: list[str] = []
    for raw in summary.bullets:
        line = re.sub(r"\s+", " ", str(raw).strip()).strip("。；;")
        if not line or line in items:
            continue
        items.append(line)
        if len(items) >= limit:
            break
    return items


def _display_bullets_for_telegram(summary: SummaryResult, limit: int = 3) -> tuple[list[str], list[str]]:
    content: list[str] = []
    links: list[str] = []
    for raw in _compact_bullets(summary, limit=8):
        line = re.sub(r"\s+", " ", str(raw).strip()).strip("。；;")
        if not line:
            continue
        if line.startswith(("关键链接:", "视频链接:", "GitHub地址:", "仓库地址:", "文档链接:")):
            links.append(line.split(":", 1)[1].strip())
            continue
        content.append(line)
        if len(content) >= limit:
            break
    dedup_links: list[str] = []
    for item in links:
        if item and item not in dedup_links:
            dedup_links.append(item)
    return content[:limit], dedup_links[:2]


def _one_line_summary(text: str, limit: int = 120) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip())
    if not value:
        return "未提取到有效结论。"
    parts = re.split(r"[。！？!?]", value)
    first = next((part.strip() for part in parts if part.strip()), value)
    return _truncate_for_telegram(first, limit)


def _extract_priority_project_lines(summary: SummaryResult) -> list[str]:
    project = ""
    repo_url = ""
    for bullet in summary.bullets:
        line = re.sub(r"\s+", " ", str(bullet).strip())
        if not line:
            continue
        if not project and (line.startswith("项目名称:") or line.startswith("项目仓库:") or line.startswith("项目:")):
            project = line.split(":", 1)[1].strip()
        if not repo_url and (
            "github.com/" in line or line.startswith("GitHub地址:") or line.startswith("仓库地址:") or line.startswith("链接:")
        ):
            if ":" in line:
                repo_url = line.split(":", 1)[1].strip()
            else:
                repo_url = line
    lines: list[str] = []
    if project:
        lines.append(f"项目: {project}")
    if repo_url:
        lines.append(f"GitHub: {repo_url}")
    return lines


def _brief_value_line(summary: SummaryResult) -> str:
    bullets = [re.sub(r"\s+", " ", str(item).strip()) for item in summary.bullets if str(item).strip()]
    corpus = "\n".join(bullets).lower()
    if any(token in corpus for token in ["安装方法:", "关键命令:", "/install-skill"]):
        return "对你有用: 很快就能判断要不要装，真要动手也知道先做什么。"
    if any(token in corpus for token in ["视频链接:", "关键链接:"]) and summary.coverage == "partial":
        return "对你有用: 先粗筛值不值得回看，别急着把它当最终结论。"
    if any(token in corpus for token in ["视频链接:", "关键链接:"]):
        return "对你有用: 先判断这条视频值不值得放进待看清单。"
    if any(token in corpus for token in ["项目名称:", "github地址:"]):
        return "对你有用: 很快就能看出这个项目是该继续跟，还是先留档。"
    return "对你有用: 先帮你把最该记住的结论和下一步拎出来。"


def _recommendation_line(summary: SummaryResult) -> str:
    mapping = {
        "must_read": "建议：强烈推荐",
        "recommended": "建议：值得看",
        "optional": "建议：按需看",
        "skip": "建议：可以先跳过",
    }
    return mapping.get(summary.recommendation_level, "建议：按需看")


def _is_videoish(summary: SummaryResult, ingest: IngestRequest) -> bool:
    url = (ingest.source_url or "").lower()
    if ingest.source_kind == "video_url":
        return True
    return any(domain in url for domain in ["bilibili.com", "youtu.be", "youtube.com", "xiaohongshu.com/explore"])


def _is_install_like(summary: SummaryResult, ingest: IngestRequest) -> bool:
    corpus = "\n".join([summary.title, summary.conclusion, *summary.bullets, *summary.follow_up_actions]).lower()
    return any(token in corpus for token in ["安装", "部署", "onboard", "gateway", "/install-skill", "配对"])


def _what_is_it_line(ingest: IngestRequest, summary: SummaryResult) -> str:
    url = (ingest.source_url or "").strip().lower()
    topic = re.sub(r"\s+", " ", (summary.primary_topic or summary.title or "这条内容").strip())
    if "docs.openclaw.ai" in url:
        return "OpenClaw 是一个跨平台 AI 代理网关，这条是它的官方安装文档。"
    if "github.com" in url and "/blob/" in url:
        return f"这是一个 GitHub 文档页，主题是 {topic}。"
    if "github.com" in url:
        return f"这是一个 GitHub 项目/仓库，主题是 {topic}。"
    if _is_videoish(summary, ingest):
        return f"这是一条关于 {topic} 的视频。"
    if _is_install_like(summary, ingest):
        return "这是一份安装/上手文档，重点是怎么把东西跑起来。"
    if ingest.source_kind == "pasted_text":
        return f"这是一段整理过的文字说明，主题是 {topic}。"
    if ingest.source_kind == "image":
        return f"这是一组截图，核心信息围绕 {topic}。"
    return f"这是一条关于 {topic} 的内容。"


def _worth_it_line(ingest: IngestRequest, summary: SummaryResult) -> str:
    url = (ingest.source_url or "").strip().lower()
    if "docs.openclaw.ai" in url:
        return "值不值得看：值得。如果你准备第一次上手，直接看官方文档最省事。"
    mapping = {
        "must_read": "值不值得看：值得，建议优先看。",
        "recommended": "值不值得看：值得，有空优先看。",
        "optional": "值不值得看：按需看，取决于你现在有没有相关需求。",
        "skip": "值不值得看：可以先跳过，除非你正好在做这件事。",
    }
    return mapping.get(summary.recommendation_level, "值不值得看：按需看。")


def _why_it_matters_line(ingest: IngestRequest, summary: SummaryResult) -> str:
    url = (ingest.source_url or "").strip().lower()
    if "docs.openclaw.ai" in url:
        return "为什么值得关注：它直接告诉你 OpenClaw 是什么、怎么装、怎么开始用。"
    bullets = [re.sub(r"\s+", " ", str(item).strip()) for item in summary.bullets if str(item).strip()]
    corpus = "\n".join(bullets).lower()
    if any(token in corpus for token in ["安装方法:", "关键命令:", "/install-skill"]):
        return "为什么值得关注：可以很快判断要不要装，真要动手时也知道先做什么。"
    if _is_videoish(summary, ingest) and summary.coverage == "partial":
        return "为什么值得关注：现在更适合先粗筛，别急着把它当最终结论。"
    if _is_videoish(summary, ingest):
        return "为什么值得关注：能帮你快速判断这条视频有没有继续看的价值。"
    if any(token in corpus for token in ["项目名称:", "github地址:"]):
        return "为什么值得关注：能很快看出这个项目值不值得继续跟。"
    return "为什么值得关注：先把最该记住的结论和下一步拎出来了。"


def _jarvis_intro_line() -> str:
    return "Sir，已处理完毕。"


def _jarvis_brief_line(ingest: IngestRequest, summary: SummaryResult) -> str:
    return _what_is_it_line(ingest, summary)


def _jarvis_judgment_line(ingest: IngestRequest, summary: SummaryResult) -> str:
    judgment = re.sub(r"\s+", " ", str(summary.reader_judgment or "").strip())
    if judgment:
        return "我的判断：" + judgment
    return "我的判断：" + _worth_it_line(ingest, summary).replace("值不值得看：", "")


def _jarvis_action_line(summary: SummaryResult) -> str:
    if summary.follow_up_actions:
        action = re.sub(r"\s+", " ", str(summary.follow_up_actions[0]).strip()).strip("。；;")
        if action:
            return "下一步建议：" + action
    return ""


def _jarvis_key_points_block(summary: SummaryResult, limit: int = 3) -> list[str]:
    bullets, _ = _display_bullets_for_telegram(summary, limit=limit)
    return [f"{idx + 1}. {item}" for idx, item in enumerate(bullets)]


class TelegramNotifier:
    def __init__(self, bot_token: str) -> None:
        self.bot_token = bot_token

    def send_result(
        self,
        ingest: IngestRequest,
        summary: SummaryResult,
        note_path: str,
        structure_map: str,
        open_url: str,
    ) -> None:
        safe_note_path = _sanitize_for_telegram(note_path)
        bullets, link_lines = _display_bullets_for_telegram(summary, limit=3)
        safe_title = _sanitize_for_telegram(summary.title)
        project_lines = [_sanitize_for_telegram(item) for item in _extract_priority_project_lines(summary)]
        one_line = _sanitize_for_telegram(_one_line_summary(summary.conclusion, limit=140))
        link_section = ["链接", *[_sanitize_for_telegram(item) for item in link_lines], ""] if link_lines else []
        key_points = [_sanitize_for_telegram(item) for item in _jarvis_key_points_block(summary, limit=3)]
        action_line = _sanitize_for_telegram(_jarvis_action_line(summary)) if _jarvis_action_line(summary) else ""
        text = "\n".join(
            [
                _jarvis_intro_line(),
                "",
                f"主题：{safe_title}",
                _sanitize_for_telegram(_jarvis_brief_line(ingest, summary)),
                f"一句话结论：{one_line}",
                _sanitize_for_telegram(_jarvis_judgment_line(ingest, summary)),
                _sanitize_for_telegram(_why_it_matters_line(ingest, summary)),
                _sanitize_for_telegram(_recommendation_line(summary)),
                action_line,
                *project_lines,
                "",
                *link_section,
                "关键点：",
                *key_points,
                "",
                "已归档到 Obsidian：",
                safe_note_path,
                f"本地打开: {open_url}",
                f"证据状态: {summary.coverage}, {summary.confidence}",
            ]
        )
        if len(text) > 3500:
            text = "\n".join(
                [
                    _jarvis_intro_line(),
                    "",
                    f"主题：{safe_title}",
                    _sanitize_for_telegram(_jarvis_brief_line(ingest, summary)),
                    f"一句话结论：{one_line}",
                    _sanitize_for_telegram(_jarvis_judgment_line(ingest, summary)),
                    _sanitize_for_telegram(_recommendation_line(summary)),
                    action_line,
                    *project_lines[:2],
                    "",
                    *link_section,
                    "关键点：",
                    *key_points[:2],
                    "",
                    "已归档到 Obsidian：",
                    safe_note_path,
                    f"本地打开: {open_url}",
                    f"证据状态: {summary.coverage}, {summary.confidence}",
                ]
            )
        payload = {
            "chat_id": ingest.chat_id,
            "text": text,
        }
        if ingest.reply_to_message_id:
            payload["reply_to_message_id"] = ingest.reply_to_message_id
        data = urlparse.urlencode(payload).encode("utf-8")
        req = urlrequest.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=data,
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(f"telegram send failed: {body}")

    def send_text(self, chat_id: str, text: str, reply_to_message_id: int | None = None) -> None:
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        data = urlparse.urlencode(payload).encode("utf-8")
        req = urlrequest.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=data,
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(f"telegram send failed: {body}")
