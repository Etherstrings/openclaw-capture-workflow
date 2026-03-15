from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.extractor import _parse_video_text_output


def _load_script_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "video_audio_asr.py"
    spec = importlib.util.spec_from_file_location("video_audio_asr_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load video_audio_asr.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


video_audio_asr = _load_script_module()


class VideoAudioASRScriptTests(unittest.TestCase):
    def test_auto_backend_prefers_apple_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_file = Path(tmp) / "audio.m4s"
            apple_input = Path(tmp) / "audio-apple.m4a"
            audio_file.write_bytes(b"audio")
            apple_raw = {
                "text": "苹果转写正文",
                "language": "zh_cn",
                "duration_seconds": 12.345,
                "segments": [{"start": 0.0, "end": 2.0, "text": "苹果转写正文"}],
                "model": "apple_speechtranscriber",
            }
            with patch.object(video_audio_asr, "_apple_backend_support_status", return_value=(True, "zh_CN")), patch.object(
                video_audio_asr,
                "_prepare_apple_audio_input",
                return_value=apple_input,
            ) as mocked_prepare_apple, patch.object(
                video_audio_asr,
                "_transcribe_with_apple",
                return_value=apple_raw,
            ) as mocked_apple, patch.object(video_audio_asr, "_prepare_remote_audio_input") as mocked_prepare_remote, patch.object(
                video_audio_asr,
                "_transcribe_remote",
            ) as mocked_remote:
                payload = video_audio_asr._transcribe_downloaded_audio(
                    audio_file,
                    backend="auto",
                    max_seconds=0.0,
                    api_base_url="https://example.com/v1",
                    api_key="",
                    model="whisper-1",
                    language="",
                    temp_dir=Path(tmp),
                )
            self.assertEqual(payload["backend"], "apple")
            self.assertEqual(payload["model"], "apple_speechtranscriber")
            self.assertEqual(payload["text"], "苹果转写正文")
            mocked_prepare_apple.assert_called_once_with(audio_file, tmp=Path(tmp), max_seconds=0.0)
            mocked_apple.assert_called_once_with(apple_input, language="")
            mocked_prepare_remote.assert_not_called()
            mocked_remote.assert_not_called()

    def test_auto_backend_falls_back_to_remote_when_apple_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_file = Path(tmp) / "audio.m4s"
            apple_input = Path(tmp) / "audio-apple.m4a"
            remote_input = Path(tmp) / "audio-remote.mp3"
            audio_file.write_bytes(b"audio")
            remote_raw = {
                "text": "远端转写正文",
                "language": "zh",
                "duration": 31.0,
                "segments": [{"start": 2.0, "end": 5.0, "text": "远端转写正文"}],
            }
            with patch.object(video_audio_asr, "_apple_backend_support_status", return_value=(True, "zh_CN")), patch.object(
                video_audio_asr,
                "_prepare_apple_audio_input",
                return_value=apple_input,
            ), patch.object(
                video_audio_asr,
                "_transcribe_with_apple",
                side_effect=RuntimeError("apple speech transcription failed"),
            ), patch.object(
                video_audio_asr,
                "_prepare_remote_audio_input",
                return_value=remote_input,
            ) as mocked_prepare_remote, patch.object(
                video_audio_asr,
                "_transcribe_remote",
                return_value=remote_raw,
            ) as mocked_remote:
                payload = video_audio_asr._transcribe_downloaded_audio(
                    audio_file,
                    backend="auto",
                    max_seconds=90.0,
                    api_base_url="https://example.com/v1",
                    api_key="dummy-key",
                    model="whisper-1",
                    language="zh",
                    temp_dir=Path(tmp),
                )
            self.assertEqual(payload["backend"], "remote")
            self.assertEqual(payload["model"], "whisper-1")
            self.assertIn("apple speech transcription failed", payload.get("fallback_reason", ""))
            mocked_prepare_remote.assert_called_once_with(audio_file, tmp=Path(tmp), max_seconds=90.0)
            mocked_remote.assert_called_once_with(
                remote_input,
                api_base_url="https://example.com/v1",
                api_key="dummy-key",
                model="whisper-1",
                language="zh",
            )

    def test_explicit_apple_backend_failure_does_not_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_file = Path(tmp) / "audio.m4s"
            audio_file.write_bytes(b"audio")
            with patch.object(
                video_audio_asr,
                "_apple_backend_support_status",
                return_value=(False, "apple speech backend requires macOS 26+"),
            ), patch.object(video_audio_asr, "_prepare_remote_audio_input") as mocked_prepare_remote, patch.object(
                video_audio_asr,
                "_transcribe_remote",
            ) as mocked_remote:
                with self.assertRaisesRegex(RuntimeError, "requires macOS 26\\+"):
                    video_audio_asr._transcribe_downloaded_audio(
                        audio_file,
                        backend="apple",
                        max_seconds=0.0,
                        api_base_url="https://example.com/v1",
                        api_key="dummy-key",
                        model="whisper-1",
                        language="zh",
                        temp_dir=Path(tmp),
                    )
            mocked_prepare_remote.assert_not_called()
            mocked_remote.assert_not_called()

    def test_apple_helper_payload_is_parseable_by_extractor(self) -> None:
        apple_payload = {
            "text": "",
            "language": "zh_cn",
            "duration_seconds": 420.024,
            "segments": [
                {
                    "start": 0.0,
                    "end": 15.48,
                    "text": "今天给大家介绍一个量化玩法。",
                },
                {
                    "start": 15.48,
                    "end": 29.46,
                    "text": "会在每天早上开盘之前给你买入或者持有建议。",
                },
                {
                    "start": 198.66,
                    "end": 215.64,
                    "text": "我把我的 github 部署和自动流程都接起来了。",
                },
            ],
            "model": "apple_speechtranscriber",
        }
        normalized = video_audio_asr._normalize_transcription_payload(
            apple_payload,
            default_model="apple_speechtranscriber",
        )
        text, metadata = _parse_video_text_output(json.dumps(normalized, ensure_ascii=False))
        self.assertIn("开盘之前", text)
        self.assertIn("github", text.lower())
        self.assertEqual(metadata.get("language"), "zh_cn")
        self.assertAlmostEqual(float(metadata.get("duration_seconds", 0.0)), 420.024, places=3)
        self.assertEqual(len(metadata.get("timeline_lines") or []), 3)


if __name__ == "__main__":
    unittest.main()
