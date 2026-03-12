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
        bullets = _compact_bullets(summary, limit=4)
        safe_title = _sanitize_for_telegram(summary.title)
        project_lines = [_sanitize_for_telegram(item) for item in _extract_priority_project_lines(summary)]
        one_line = _sanitize_for_telegram(_one_line_summary(summary.conclusion, limit=140))
        text = "\n".join(
            [
                "识别完成",
                f"标题: {safe_title}",
                f"一句话: {one_line}",
                *project_lines,
                "",
                "关键事实",
                *[f"{idx + 1}. {bullet}" for idx, bullet in enumerate(bullets)],
                "",
                "笔记路径",
                safe_note_path,
                f"本地打开: {open_url}",
                f"状态: {summary.coverage}, {summary.confidence}",
            ]
        )
        if len(text) > 3500:
            text = "\n".join(
                [
                    "识别完成",
                    f"标题: {safe_title}",
                    f"一句话: {one_line}",
                    *project_lines[:2],
                    "",
                    "关键事实",
                    *[f"{idx + 1}. {bullet}" for idx, bullet in enumerate(bullets[:2])],
                    "",
                    "笔记路径",
                    safe_note_path,
                    f"本地打开: {open_url}",
                    f"状态: {summary.coverage}, {summary.confidence}",
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
