import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.analyzer.models import CollectedVideo, VideoResult
from openclaw_capture_workflow.analyzer.video import process_videos
from openclaw_capture_workflow.config import AppConfig, ExtractorConfig, ObsidianConfig, SummarizerConfig, TelegramConfig


def _config(tmp: str) -> AppConfig:
    return AppConfig(
        listen_host="127.0.0.1",
        listen_port=8765,
        state_dir="state",
        obsidian=ObsidianConfig(
            vault_path=tmp,
            inbox_root="Inbox/OpenClaw",
            topics_root="Topics",
            entities_root="Entities",
            auto_topic_whitelist=[],
            auto_topic_blocklist=[],
        ),
        telegram=TelegramConfig(result_bot_token="token"),
        summarizer=SummarizerConfig(api_base_url="https://example.com/v1", api_key="key", model="unused", timeout_seconds=30),
        extractors=ExtractorConfig(video_keyframes_command="python3 scripts/video_keyframes_extract.py --url \"{url}\" --output-path \"{output_path}\" --max-seconds \"{max_seconds}\" --max-frames \"{max_frames}\""),
    )


class AnalyzerVideoTest(unittest.TestCase):
    def test_process_videos_populates_duration_and_frames(self) -> None:
        video = CollectedVideo(result=VideoResult(src="https://example.com/demo.mp4"))
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            downloaded = tmp_path / "downloaded.mp4"
            downloaded.write_bytes(b"fake-video")
            frame_dir = tmp_path / "frames"
            frame_dir.mkdir()
            frame_path = frame_dir / "frame-001.jpg"
            frame_path.write_bytes(b"frame")

            with patch("openclaw_capture_workflow.analyzer.video.download_video", return_value=downloaded), patch(
                "openclaw_capture_workflow.analyzer.video.probe_duration_seconds", return_value=12.5
            ), patch(
                "openclaw_capture_workflow.analyzer.video.sample_video_frames", return_value=[frame_path]
            ):
                processed, warnings = process_videos([video], tmp_path, max_video_frames=4, config=_config(tmp))

        self.assertEqual(warnings, [])
        self.assertEqual(processed[0].result.duration_seconds, 12.5)
        self.assertEqual(processed[0].frame_paths, [frame_path])
        self.assertTrue(processed[0].result.frame_summaries)

    def test_process_videos_collects_warning_on_failure(self) -> None:
        video = CollectedVideo(result=VideoResult(src="https://example.com/demo.mp4"))
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch(
                "openclaw_capture_workflow.analyzer.video.download_video",
                side_effect=RuntimeError("download failed"),
            ):
                processed, warnings = process_videos([video], tmp_path, max_video_frames=4, config=_config(tmp))

        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0].frame_paths, [])
        self.assertTrue(warnings)
        self.assertIn("video_download_failed", warnings[0])

    def test_process_videos_uses_extractor_command_for_non_direct_urls(self) -> None:
        video = CollectedVideo(result=VideoResult(src="https://www.youtube.com/watch?v=demo"))
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            frame_dir = tmp_path / "frames"
            frame_dir.mkdir()
            frame_path = frame_dir / "frame-001.jpg"
            frame_path.write_bytes(b"frame")
            with patch(
                "openclaw_capture_workflow.analyzer.video._run_keyframe_command",
                return_value=[frame_path],
            ):
                processed, warnings = process_videos([video], tmp_path, max_video_frames=4, config=_config(tmp))

        self.assertEqual(warnings, [])
        self.assertEqual(processed[0].frame_paths, [frame_path])
        self.assertTrue(processed[0].result.frame_summaries)


if __name__ == "__main__":
    unittest.main()
