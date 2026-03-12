#!/usr/bin/env python3
"""Extract subtitles for a video URL and print normalized JSON."""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib import parse as urlparse
from urllib import request as urlrequest


TIMESTAMP_RE = re.compile(
    r"^\s*(\d{1,2}:\d{2}:\d{2}(?:[.,]\d{1,3})?|\d{1,2}:\d{2}(?:[.,]\d{1,3})?)\s+-->\s+"
    r"(\d{1,2}:\d{2}:\d{2}(?:[.,]\d{1,3})?|\d{1,2}:\d{2}(?:[.,]\d{1,3})?)"
)
BVID_RE = re.compile(r"\b(BV[0-9A-Za-z]{10,})\b", re.IGNORECASE)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--max-seconds", type=float, default=0.0)
    parser.add_argument("--lang-priority", default="zh,zh-hans,zh-cn,en")
    parser.add_argument("--cookies-from-browser", default=os.getenv("VIDEO_COOKIES_FROM_BROWSER", ""))
    parser.add_argument("--cookies", default=os.getenv("VIDEO_COOKIES_PATH", ""))
    return parser.parse_args()


def _run(args: list[str]) -> None:
    try:
        subprocess.run(args, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        msg = stderr or stdout or str(exc)
        raise RuntimeError(f"command failed: {' '.join(args)} | {msg}") from exc


def _yt_dlp_cmd() -> list[str]:
    binary = shutil.which("yt-dlp")
    if binary:
        return [binary]
    return [sys.executable, "-m", "yt_dlp"]


def _is_youtube_url(url: str) -> bool:
    lowered = (url or "").lower()
    return "youtube.com/" in lowered or "youtu.be/" in lowered


def _split_cookie_browsers(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _should_retry_with_cookies(error_text: str) -> bool:
    lowered = (error_text or "").lower()
    return any(
        token in lowered
        for token in [
            "sign in to confirm you’re not a bot",
            "sign in to confirm you're not a bot",
            "use --cookies-from-browser",
            "login required",
            "authentication",
        ]
    )


def _run_yt_dlp_with_auth_retry(
    cmd_prefix: list[str],
    *,
    url: str,
    cookies_from_browser: str,
    cookies_file: str,
    auto_cookie_on_youtube: bool = True,
) -> None:
    initial_error = ""
    last_error = ""
    try:
        _run([*cmd_prefix, url])
        return
    except Exception as exc:  # pragma: no cover - network/platform-dependent
        initial_error = str(exc)
        last_error = initial_error

    cookie_attempts: list[list[str]] = []
    if cookies_file:
        cookie_attempts.append(["--cookies", cookies_file])
    for browser in _split_cookie_browsers(cookies_from_browser):
        cookie_attempts.append(["--cookies-from-browser", browser])
    if (
        auto_cookie_on_youtube
        and _is_youtube_url(url)
        and _should_retry_with_cookies(initial_error)
        and not cookie_attempts
    ):
        cookie_attempts.append(["--cookies-from-browser", "chrome"])
        cookie_attempts.append(["--cookies-from-browser", "chromium"])
        cookie_attempts.append(["--cookies-from-browser", "edge"])

    for auth_args in cookie_attempts:
        try:
            _run([*cmd_prefix, *auth_args, url])
            return
        except Exception as exc:  # pragma: no cover - network/platform-dependent
            last_error = str(exc)
    if initial_error and last_error and last_error != initial_error:
        raise RuntimeError(f"{initial_error} | cookie_retry_failed: {last_error}")
    raise RuntimeError(last_error or initial_error)


def _http_json(url: str, headers: dict[str, str] | None = None) -> dict:
    req = urlrequest.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urlrequest.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
    return json.loads(raw)


def _extract_bvid(source_url: str) -> str | None:
    match = BVID_RE.search(source_url or "")
    if match:
        return match.group(1)
    parsed = urlparse.urlsplit(source_url or "")
    query = urlparse.parse_qs(parsed.query)
    bvids = query.get("bvid") or []
    return bvids[0] if bvids else None


def _fetch_bilibili_subtitles(bvid: str, max_seconds: float, lang_priority: list[str]) -> tuple[str, str, float | None, list[dict]]:
    view = _http_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
    if int(view.get("code", -1)) != 0:
        return "", "", None, []
    data = view.get("data", {}) if isinstance(view.get("data"), dict) else {}
    pages = data.get("pages") or []
    if not pages or not isinstance(pages[0], dict):
        return "", "", None, []
    cid = pages[0].get("cid")
    if not cid:
        return "", "", None, []

    player = _http_json(
        f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": f"https://www.bilibili.com/video/{bvid}"},
    )
    if int(player.get("code", -1)) != 0:
        return "", "", None, []
    subtitle_info = ((player.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
    if not isinstance(subtitle_info, list) or not subtitle_info:
        return "", "", None, []

    def score(item: dict) -> tuple[int, int]:
        lan = str(item.get("lan", "")).lower()
        rank = len(lang_priority) + 5
        for idx, prefix in enumerate(lang_priority):
            if lan.startswith(prefix):
                rank = idx
                break
        ai_type = int(item.get("ai_type", 0) or 0)
        return (rank, ai_type)

    subtitle_info = [item for item in subtitle_info if isinstance(item, dict) and item.get("subtitle_url")]
    if not subtitle_info:
        return "", "", None, []
    subtitle_info.sort(key=score)
    chosen = subtitle_info[0]
    subtitle_url = str(chosen.get("subtitle_url", ""))
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url
    if not subtitle_url.startswith("http"):
        return "", "", None, []

    subtitle_json = _http_json(subtitle_url, headers={"User-Agent": "Mozilla/5.0", "Referer": f"https://www.bilibili.com/video/{bvid}"})
    body = subtitle_json.get("body") or []
    if not isinstance(body, list):
        return "", "", None, []
    segments: list[dict] = []
    max_end = 0.0
    for item in body:
        if not isinstance(item, dict):
            continue
        text = _clean_caption_line(str(item.get("content", "")))
        if not text:
            continue
        try:
            start = float(item.get("from", 0.0))
        except (TypeError, ValueError):
            start = 0.0
        try:
            end = float(item.get("to", start))
        except (TypeError, ValueError):
            end = start
        if max_seconds > 0 and start >= max_seconds:
            continue
        segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        max_end = max(max_end, end)
        if len(segments) >= 3000:
            break
    deduped: list[dict] = []
    seen: set[str] = set()
    for seg in segments:
        text = str(seg["text"]).strip()
        if text in seen:
            continue
        seen.add(text)
        deduped.append(seg)
    text = "\n".join(str(item["text"]) for item in deduped).strip()
    return text, str(chosen.get("lan", "")).lower(), (round(max_end, 3) if max_end > 0 else None), deduped[:1200]


def _parse_timestamp(value: str) -> float:
    token = value.replace(",", ".").strip()
    parts = token.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise ValueError(f"invalid timestamp: {value}")


def _clean_caption_line(line: str) -> str:
    value = html.unescape(line.strip())
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _extract_language_hint(path: Path) -> str:
    name = path.name.lower()
    # examples: x.zh-Hans.vtt / x.en.vtt / x.zh-CN-orig.vtt
    match = re.search(r"\.([a-z]{2}(?:-[a-z0-9]+)?)", name)
    return (match.group(1) if match else "").lower()


def _subtitle_score(path: Path, lang_priority: list[str]) -> tuple[int, int, int]:
    name = path.name.lower()
    lang = _extract_language_hint(path)
    rank = len(lang_priority) + 5
    for idx, prefix in enumerate(lang_priority):
        if lang.startswith(prefix):
            rank = idx
            break
    is_live_chat = 1 if "live_chat" in name else 0
    is_auto = 1 if ".orig." in name or ".auto." in name else 0
    return (is_live_chat, rank, is_auto)


def _build_sub_langs(lang_priority: list[str]) -> str:
    entries: list[str] = []
    for lang in lang_priority:
        key = lang.strip().lower()
        if not key:
            continue
        if key == "zh":
            entries.extend(["zh", "zh-Hans", "zh-Hant", "zh-CN", "zh-TW", "zh-*"])
            continue
        if key == "en":
            entries.extend(["en", "en-*"])
            continue
        entries.append(key)
    if "en" not in [item.lower() for item in entries]:
        entries.extend(["en", "en-*"])
    deduped: list[str] = []
    seen: set[str] = set()
    for item in entries:
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(item)
        if len(deduped) >= 14:
            break
    return ",".join(deduped) if deduped else "zh,zh-Hans,zh-Hant,en,en-*"


def _parse_vtt(path: Path, max_seconds: float) -> tuple[list[dict[str, float | str]], float]:
    segments: list[dict[str, float | str]] = []
    max_end = 0.0
    current_start = None
    current_end = None
    current_lines: list[str] = []
    raw_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for raw in raw_lines + [""]:
        line = raw.rstrip("\n")
        match = TIMESTAMP_RE.match(line)
        if match:
            if current_start is not None and current_lines:
                text = " ".join(current_lines).strip()
                if text and (max_seconds <= 0 or current_start < max_seconds):
                    segments.append(
                        {
                            "start": round(current_start, 3),
                            "end": round(current_end or current_start, 3),
                            "text": text,
                        }
                    )
                    max_end = max(max_end, float(current_end or current_start))
            current_start = _parse_timestamp(match.group(1))
            current_end = _parse_timestamp(match.group(2))
            current_lines = []
            continue
        stripped = line.strip()
        if not stripped:
            if current_start is not None and current_lines:
                text = " ".join(current_lines).strip()
                if text and (max_seconds <= 0 or current_start < max_seconds):
                    segments.append(
                        {
                            "start": round(current_start, 3),
                            "end": round(current_end or current_start, 3),
                            "text": text,
                        }
                    )
                    max_end = max(max_end, float(current_end or current_start))
            current_start = None
            current_end = None
            current_lines = []
            continue
        if stripped.isdigit() or stripped.startswith(("WEBVTT", "NOTE", "Kind:", "Language:")):
            continue
        cleaned = _clean_caption_line(stripped)
        if cleaned:
            current_lines.append(cleaned)
    deduped: list[dict[str, float | str]] = []
    seen_text: set[str] = set()
    for seg in segments:
        text = str(seg["text"]).strip()
        if not text or text in seen_text:
            continue
        seen_text.add(text)
        deduped.append(seg)
        if len(deduped) >= 3000:
            break
    return deduped, max_end


def main() -> int:
    args = _parse_args()
    lang_priority = [item.strip().lower() for item in args.lang_priority.split(",") if item.strip()]
    bvid = _extract_bvid(args.url)
    if bvid:
        try:
            text, language, duration, segments = _fetch_bilibili_subtitles(
                bvid,
                max(0.0, float(args.max_seconds)),
                lang_priority,
            )
            if text:
                print(
                    json.dumps(
                        {
                            "text": text[:200000],
                            "language": language,
                            "duration_seconds": duration,
                            "segments": segments,
                            "source": "bilibili_api",
                        },
                        ensure_ascii=False,
                    )
                )
                return 0
        except Exception:
            # Fallback to yt-dlp path if Bilibili API path fails.
            pass
    with tempfile.TemporaryDirectory(prefix="oc-subtitle-") as tmp:
        out_tpl = str(Path(tmp) / "%(id)s.%(ext)s")
        cmd_prefix = [
            *_yt_dlp_cmd(),
            "--skip-download",
            "--write-sub",
            "--write-auto-sub",
            "--sub-langs",
            _build_sub_langs(lang_priority),
            "--sub-format",
            "vtt",
            "--output",
            out_tpl,
        ]
        try:
            _run_yt_dlp_with_auth_retry(
                cmd_prefix,
                url=args.url,
                cookies_from_browser=args.cookies_from_browser.strip(),
                cookies_file=args.cookies.strip(),
            )
        except Exception:
            # Keep extractor chain robust: empty subtitle allows ASR fallback.
            print(json.dumps({"text": "", "language": "", "duration_seconds": None, "segments": []}, ensure_ascii=False))
            return 0

        vtt_files = [path for path in Path(tmp).glob("*.vtt") if path.is_file()]
        if not vtt_files:
            print(json.dumps({"text": "", "language": "", "duration_seconds": None, "segments": []}, ensure_ascii=False))
            return 0

        vtt_files.sort(key=lambda item: _subtitle_score(item, lang_priority))
        selected = vtt_files[0]
        segments, duration = _parse_vtt(selected, max(0.0, float(args.max_seconds)))
        text = "\n".join(str(item["text"]) for item in segments).strip()
        payload = {
            "text": text[:200000],
            "language": _extract_language_hint(selected),
            "duration_seconds": round(duration, 3) if duration > 0 else None,
            "segments": segments[:1200],
            "source": selected.name,
        }
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
