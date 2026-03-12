#!/usr/bin/env python3
"""Download video audio, transcribe with OpenAI-compatible STT, print normalized JSON."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from urllib import parse as urlparse
from urllib import error as urlerror
from urllib import request as urlrequest

BVID_RE = re.compile(r"\b(BV[0-9A-Za-z]{10,})\b", re.IGNORECASE)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--max-seconds", type=float, default=0.0)
    parser.add_argument("--api-base-url", default=os.getenv("STT_API_BASE_URL", "https://aihubmix.com/v1"))
    parser.add_argument("--api-key", default=os.getenv("STT_API_KEY", os.getenv("AIHUBMIX_API_KEY", "")))
    parser.add_argument("--model", default=os.getenv("STT_MODEL", "whisper-1"))
    parser.add_argument("--language", default=os.getenv("STT_LANGUAGE", ""))
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
    return "bilibili.com/" in (url or "").lower() or "b23.tv/" in (url or "").lower()


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


def _ffmpeg_location_for_ytdlp() -> str | None:
    direct = shutil.which("ffmpeg")
    if direct and Path(direct).exists():
        return str(Path(direct).resolve())
    for python_bin in _python_candidates():
        if not _python_has_module(python_bin, "imageio_ffmpeg"):
            continue
        ffmpeg_path = _python_imageio_ffmpeg_path(python_bin)
        if ffmpeg_path:
            return ffmpeg_path
    return None


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
    if _is_youtube_url(url) and _should_retry_with_cookies(initial_error) and not cookie_attempts:
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


def _download_bilibili_audio(url: str, tmp: Path) -> Path | None:
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
    audio_list = dash.get("audio") or []
    if not isinstance(audio_list, list) or not audio_list:
        return None
    audio_list = [item for item in audio_list if isinstance(item, dict) and item.get("baseUrl")]
    if not audio_list:
        return None
    audio_list.sort(key=lambda item: int(item.get("bandwidth", 0) or 0), reverse=True)
    output = tmp / "audio-bili.m4s"
    chosen = audio_list[0]
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


def _pick_downloaded_audio(tmp: Path) -> Path | None:
    candidates = []
    for ext in ("m4a", "mp3", "webm", "opus", "wav"):
        candidates.extend(tmp.glob(f"audio*.{ext}"))
    for path in tmp.iterdir():
        if path.is_file() and path.suffix.lower().lstrip(".") in {"m4a", "mp3", "webm", "opus", "wav"}:
            candidates.append(path)
    if not candidates:
        return None
    candidates = sorted(set(candidates), key=lambda item: item.stat().st_size, reverse=True)
    return candidates[0]


def _build_multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    boundary = "----OpenClawBoundary7MA4YWxkTrZu0gW"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(file_path.read_bytes())
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(chunks)
    return body, boundary


def _transcribe(audio_path: Path, *, api_base_url: str, api_key: str, model: str, language: str) -> dict[str, Any]:
    if not api_key:
        raise RuntimeError("missing STT api key (set STT_API_KEY or AIHUBMIX_API_KEY)")
    endpoint = api_base_url.rstrip("/") + "/audio/transcriptions"
    fields = {"model": model, "response_format": "verbose_json"}
    if language:
        fields["language"] = language
    body, boundary = _build_multipart(fields, audio_path)
    req = urlrequest.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=300) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        detail = ""
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
            detail = body[:600]
        except Exception:
            detail = ""
        suffix = f" | {detail}" if detail else ""
        raise RuntimeError(f"stt request failed: status={exc.code}{suffix}") from exc
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError("unexpected STT response format")
    return payload


def _normalize_segments(segments: Any) -> list[dict[str, float | str]]:
    if not isinstance(segments, list):
        return []
    out: list[dict[str, float | str]] = []
    for item in segments:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        try:
            start = float(item.get("start", 0.0))
        except (TypeError, ValueError):
            start = 0.0
        try:
            end = float(item.get("end", start))
        except (TypeError, ValueError):
            end = start
        out.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        if len(out) >= 2000:
            break
    return out


def _estimate_duration_from_segments(segments: list[dict[str, float | str]]) -> float | None:
    if not segments:
        return None
    end_points = [float(item["end"]) for item in segments if isinstance(item.get("end"), (int, float))]
    if not end_points:
        return None
    return round(max(end_points), 3)


def main() -> int:
    args = _parse_args()
    with tempfile.TemporaryDirectory(prefix="oc-audio-") as tmp_dir:
        tmp = Path(tmp_dir)
        audio_file = None
        try:
            audio_file = _download_bilibili_audio(args.url, tmp)
        except Exception:
            audio_file = None
        if audio_file is None:
            output_tpl = str(tmp / "audio.%(ext)s")
            ffmpeg_location = _ffmpeg_location_for_ytdlp()
            ytdlp_opts = [
                "-f",
                "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
                "--output",
                output_tpl,
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
            audio_file = _pick_downloaded_audio(tmp)
        if not audio_file:
            raise RuntimeError("audio download failed: no audio file found")

        asr_input = tmp / "audio-normalized.mp3"
        ffmpeg_args = [
            *_ffmpeg_cmd(),
            "-y",
            "-i",
            str(audio_file),
            "-ac",
            "1",
            "-ar",
            "16000",
        ]
        if args.max_seconds > 0:
            ffmpeg_args.extend(["-t", str(max(1, int(args.max_seconds)))])
        ffmpeg_args.append(str(asr_input))
        _run(ffmpeg_args)
        if not asr_input.exists() or not asr_input.is_file():
            raise RuntimeError("audio normalization failed")

        raw = _transcribe(
            asr_input,
            api_base_url=args.api_base_url,
            api_key=args.api_key,
            model=args.model,
            language=args.language.strip(),
        )
        text = str(raw.get("text", "")).strip()
        segments = _normalize_segments(raw.get("segments"))
        duration = raw.get("duration")
        try:
            duration_value = round(float(duration), 3) if duration is not None else None
        except (TypeError, ValueError):
            duration_value = None
        if duration_value is None:
            duration_value = _estimate_duration_from_segments(segments)
        payload = {
            "text": re.sub(r"\s+", " ", text).strip()[:220000],
            "language": str(raw.get("language", "")).strip().lower(),
            "duration_seconds": duration_value,
            "segments": segments[:1200],
            "model": args.model,
        }
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
