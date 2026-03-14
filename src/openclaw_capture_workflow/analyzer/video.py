"""Video download and frame sampling helpers."""

from __future__ import annotations

from pathlib import Path
import shlex
import shutil
import subprocess
from typing import List, Tuple
from urllib import request as urlrequest
from urllib.parse import urlparse

from ..config import AppConfig
from .models import CollectedVideo


def _find_binary(name: str) -> str | None:
    return shutil.which(name)


def download_video(url: str, output_path: Path, timeout_seconds: int = 30) -> Path:
    req = urlrequest.Request(url, headers={"User-Agent": "Mozilla/5.0 OpenClawCaptureWorkflow/0.1"})
    with urlrequest.urlopen(req, timeout=timeout_seconds) as response:
        output_path.write_bytes(response.read())
    return output_path


def probe_duration_seconds(video_path: Path) -> float | None:
    ffprobe = _find_binary("ffprobe")
    if not ffprobe:
        return None
    result = subprocess.run(
        [
            ffprobe,
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
        return float((result.stdout or "").strip())
    except ValueError:
        return None


def sample_video_frames(video_path: Path, output_dir: Path, max_frames: int) -> List[Path]:
    ffmpeg = _find_binary("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_pattern = output_dir / "frame-%03d.jpg"
    frame_count = max(1, int(max_frames))
    result = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"fps=1/{max(1, frame_count)}",
            "-frames:v",
            str(frame_count),
            str(output_pattern),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or "ffmpeg frame sampling failed")
    return sorted(output_dir.glob("frame-*.jpg"))


def _looks_like_direct_video_url(url: str) -> bool:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix in {".mp4", ".mov", ".m4v", ".mkv", ".webm"}


def _run_keyframe_command(command_template: str, url: str, output_dir: Path, max_frames: int) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = command_template.format(
        url=url,
        output_path=str(output_dir),
        max_seconds="0",
        max_frames=str(max_frames),
    )
    result = subprocess.run(
        shlex.split(command),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        raise RuntimeError(stderr or stdout or "video keyframe command failed")
    lines = [Path(line.strip()) for line in (result.stdout or "").splitlines() if line.strip()]
    return [path for path in lines if path.exists()]


def _build_frame_summary(video: CollectedVideo) -> None:
    frame_count = len(video.frame_paths)
    if frame_count == 0 or video.result.frame_summaries:
        return
    duration = video.result.duration_seconds
    if duration is not None:
        video.result.frame_summaries = [f"成功采样 {frame_count} 帧，视频时长约 {round(duration, 1)} 秒。"]
    else:
        video.result.frame_summaries = [f"成功采样 {frame_count} 帧，可用于后续视频内容理解。"]


def process_videos(
    videos: List[CollectedVideo],
    temp_dir: Path,
    max_video_frames: int,
    config: AppConfig | None = None,
) -> Tuple[List[CollectedVideo], List[str]]:
    warnings: List[str] = []
    processed: List[CollectedVideo] = []
    videos_dir = temp_dir / "videos"
    frames_dir = temp_dir / "frames"
    for index, video in enumerate(videos, start=1):
        current = video
        try:
            if _looks_like_direct_video_url(video.result.src):
                suffix = Path(urlparse(video.result.src).path).suffix or ".mp4"
                video_path = videos_dir / f"video-{index}{suffix}"
                video_path.parent.mkdir(parents=True, exist_ok=True)
                current.local_video_path = download_video(video.result.src, video_path)
                current.result.duration_seconds = probe_duration_seconds(current.local_video_path)
                current.frame_paths = sample_video_frames(
                    current.local_video_path,
                    frames_dir / f"video-{index}",
                    max_frames=max_video_frames,
                )
            elif config and config.extractors.video_keyframes_command:
                current.frame_paths = _run_keyframe_command(
                    config.extractors.video_keyframes_command,
                    url=video.result.src,
                    output_dir=frames_dir / f"video-{index}",
                    max_frames=max_video_frames,
                )
            else:
                raise RuntimeError("video source is not a direct media URL and no extractor command is configured")
            _build_frame_summary(current)
        except Exception as exc:
            message = str(exc)
            if "download" in message.lower():
                warnings.append(f"video_download_failed:{video.result.src}:{message}")
            elif "frame" in message.lower() or "ffmpeg" in message.lower():
                warnings.append(f"video_frame_sampling_failed:{video.result.src}:{message}")
            else:
                warnings.append(f"video_processing_failed:{video.result.src}:{message}")
        processed.append(current)
    return processed, warnings
