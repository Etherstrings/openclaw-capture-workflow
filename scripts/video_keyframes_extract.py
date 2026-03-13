#!/usr/bin/env python3
"""Extract representative keyframes from a video URL and print file paths."""

from __future__ import annotations

import argparse
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

BVID_RE = re.compile(r"\b(BV[0-9A-Za-z]{10,})\b", re.IGNORECASE)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--max-seconds", type=float, default=0.0)
    parser.add_argument("--max-frames", type=int, default=8)
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


def _python_candidates() -> list[str]:
    candidates = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python3"),
        str(Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python"),
        shutil.which("python3"),
        "/Library/Developer/CommandLineTools/usr/bin/python3",
        "/usr/bin/python3",
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if not item:
            continue
        path = str(item)
        if path in seen:
            continue
        if not Path(path).exists():
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def _python_has_module(python_bin: str, module: str) -> bool:
    probe = subprocess.run(
        [python_bin, "-c", f"import {module}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def _python_imageio_ffmpeg_path(python_bin: str) -> str | None:
    probe = subprocess.run(
        [python_bin, "-c", "from imageio_ffmpeg import get_ffmpeg_exe; print(get_ffmpeg_exe())"],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return None
    value = (probe.stdout or "").strip().splitlines()
    if not value:
        return None
    path = value[-1].strip()
    if path and Path(path).exists():
        return path
    return None


def _yt_dlp_cmd() -> list[str]:
    binary = shutil.which("yt-dlp")
    if binary:
        return [binary]
    for python_bin in _python_candidates():
        if _python_has_module(python_bin, "yt_dlp"):
            return [python_bin, "-m", "yt_dlp"]
    raise RuntimeError("yt-dlp not found (binary missing and python module unavailable)")


def _ffmpeg_cmd() -> list[str]:
    for candidate in [shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if candidate and Path(candidate).exists():
            return [str(candidate)]
    for python_bin in _python_candidates():
        if not _python_has_module(python_bin, "imageio_ffmpeg"):
            continue
        ffmpeg_path = _python_imageio_ffmpeg_path(python_bin)
        if ffmpeg_path:
            return [ffmpeg_path]
    raise RuntimeError("ffmpeg not found and imageio-ffmpeg unavailable")


def _ffprobe_cmd() -> list[str] | None:
    binary = shutil.which("ffprobe")
    if binary:
        return [binary]
    ffmpeg_bin = _ffmpeg_cmd()[0]
    sibling = Path(ffmpeg_bin).with_name("ffprobe")
    if sibling.exists() and sibling.is_file():
        return [str(sibling)]
    return None


def _extract_bvid(source_url: str) -> str | None:
    match = BVID_RE.search(source_url or "")
    if match:
        return match.group(1)
    parsed = urlparse.urlsplit(source_url or "")
    query = urlparse.parse_qs(parsed.query)
    bvids = query.get("bvid") or []
    return bvids[0] if bvids else None


def _is_youtube_url(url: str) -> bool:
    lowered = (url or "").lower()
    return "youtube.com/" in lowered or "youtu.be/" in lowered


def _is_bilibili_url(url: str) -> bool:
    lowered = (url or "").lower()
    return "bilibili.com/" in lowered or "b23.tv/" in lowered


def _is_xiaohongshu_url(url: str) -> bool:
    return "xiaohongshu.com/" in (url or "").lower()


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
            "http error 403",
            "forbidden",
        ]
    )


def _yt_dlp_site_headers(url: str) -> list[str]:
    if _is_bilibili_url(url):
        return [
            "--add-header",
            "Referer: https://www.bilibili.com",
            "--add-header",
            "Origin: https://www.bilibili.com",
            "--add-header",
            "User-Agent: Mozilla/5.0",
        ]
    if _is_xiaohongshu_url(url):
        return [
            "--add-header",
            "Referer: https://www.xiaohongshu.com",
            "--add-header",
            "Origin: https://www.xiaohongshu.com",
            "--add-header",
            "User-Agent: Mozilla/5.0",
        ]
    return []


def _ffmpeg_location_for_ytdlp() -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg and Path(ffmpeg).exists():
        return str(Path(ffmpeg).resolve())
    for python_bin in _python_candidates():
        if not _python_has_module(python_bin, "imageio_ffmpeg"):
            continue
        probe = subprocess.run(
            [python_bin, "-c", "from imageio_ffmpeg import get_ffmpeg_exe; print(get_ffmpeg_exe())"],
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            continue
        candidate = (probe.stdout or "").strip().splitlines()
        if not candidate:
            continue
        path = candidate[-1].strip()
        if path and Path(path).exists():
            return path
    return None


def _run_yt_dlp_with_auth_retry(
    cmd_prefix: list[str],
    *,
    url: str,
    cookies_from_browser: str,
    cookies_file: str,
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
    if (_is_youtube_url(url) or _is_xiaohongshu_url(url)) and _should_retry_with_cookies(initial_error) and not cookie_attempts:
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
    with urlrequest.urlopen(req, timeout=40) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
    return json.loads(raw)


def _download_url_to_file(url: str, target: Path, *, referer: str | None = None) -> None:
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer:
        headers["Referer"] = referer
        parsed = urlparse.urlsplit(referer)
        if parsed.scheme and parsed.netloc:
            headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
    req = urlrequest.Request(url, headers=headers)
    with urlrequest.urlopen(req, timeout=120) as resp:
        target.write_bytes(resp.read())


def _download_bilibili_video(url: str, tmp: Path) -> Path | None:
    bvid = _extract_bvid(url)
    if not bvid:
        return None
    view = _http_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
    if int(view.get("code", -1)) != 0:
        return None
    pages = ((view.get("data") or {}).get("pages") or [])
    if not pages or not isinstance(pages[0], dict) or not pages[0].get("cid"):
        return None
    cid = pages[0]["cid"]
    playurl = _http_json(
        f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn=64&fnval=16&fourk=0",
        headers={"User-Agent": "Mozilla/5.0", "Referer": f"https://www.bilibili.com/video/{bvid}"},
    )
    if int(playurl.get("code", -1)) != 0:
        return None
    dash = ((playurl.get("data") or {}).get("dash") or {})
    video_list = dash.get("video") or []
    if not isinstance(video_list, list) or not video_list:
        return None
    video_list = [item for item in video_list if isinstance(item, dict) and item.get("baseUrl")]
    if not video_list:
        return None
    # Pick medium quality to reduce download while keeping enough detail for keyframes.
    video_list.sort(key=lambda item: int(item.get("bandwidth", 0) or 0))
    chosen = video_list[min(len(video_list) - 1, 1)]
    output = tmp / "video-bili.m4s"
    url_candidates: list[str] = []
    base = str(chosen.get("baseUrl", "")).strip()
    if base:
        url_candidates.append(base)
    backup = chosen.get("backupUrl") or chosen.get("backup_url") or []
    if isinstance(backup, list):
        for item in backup:
            candidate = str(item).strip()
            if candidate:
                url_candidates.append(candidate)
    referer = f"https://www.bilibili.com/video/{bvid}"
    for candidate in url_candidates[:5]:
        try:
            _download_url_to_file(candidate, output, referer=referer)
            if output.exists() and output.stat().st_size > 0:
                return output
        except Exception:
            continue
    return None


def _find_video_file(folder: Path) -> Path | None:
    exts = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
    candidates = [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in exts]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.stat().st_size, reverse=True)
    return candidates[0]


def _probe_duration_seconds(video_path: Path) -> float | None:
    probe_cmd = _ffprobe_cmd()
    if probe_cmd is None:
        return None
    result = subprocess.run(
        [
            *probe_cmd,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        value = float(result.stdout.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def main() -> int:
    args = _parse_args()
    output_dir = Path(args.output_path).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="oc-keyframes-") as tmp_dir:
        tmp = Path(tmp_dir)
        video_path = None
        try:
            video_path = _download_bilibili_video(args.url, tmp)
        except Exception:
            video_path = None
        if video_path is None:
            out_tpl = str(tmp / "video.%(ext)s")
            ffmpeg_location = _ffmpeg_location_for_ytdlp()
            ytdlp_opts = [
                "-f",
                "b[ext=mp4]/b[height<=1080]/best",
                "--output",
                out_tpl,
                *_yt_dlp_site_headers(args.url),
            ]
            if args.max_seconds > 0:
                section_end = max(1, int(args.max_seconds))
                if ffmpeg_location:
                    ytdlp_opts.extend(["--ffmpeg-location", ffmpeg_location, "--download-sections", f"*0-{section_end}"])
            download_cmd_prefix = [*_yt_dlp_cmd(), *ytdlp_opts]
            _run_yt_dlp_with_auth_retry(
                download_cmd_prefix,
                url=args.url,
                cookies_from_browser=args.cookies_from_browser.strip(),
                cookies_file=args.cookies.strip(),
            )
            video_path = _find_video_file(tmp)
        if not video_path:
            raise RuntimeError("video download failed: no media file found")

        duration = _probe_duration_seconds(video_path)
        max_frames = max(1, int(args.max_frames))
        if duration and duration > 0:
            interval = max(1, int(duration / max_frames))
        else:
            interval = 12

        frame_tpl = str(output_dir / "frame-%03d.jpg")
        _run(
            [
                *_ffmpeg_cmd(),
                "-y",
                "-i",
                str(video_path),
                "-vf",
                f"fps=1/{interval}",
                "-frames:v",
                str(max_frames),
                frame_tpl,
            ]
        )

    frames = sorted(output_dir.glob("frame-*.jpg"))
    for frame in frames:
        print(str(frame.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
