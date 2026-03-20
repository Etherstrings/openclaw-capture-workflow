import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.config import (
    AppConfig,
    ExtractorConfig,
    ObsidianConfig,
    SummarizerConfig,
    TelegramConfig,
)
from openclaw_capture_workflow.video_provider_router import VideoProviderBundle, VideoProviderRouter


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
            auto_topic_whitelist=["AI"],
            auto_topic_blocklist=[],
        ),
        telegram=TelegramConfig(result_bot_token="token"),
        summarizer=SummarizerConfig(api_base_url="https://example.com", api_key="k", model="m", timeout_seconds=30),
        extractors=ExtractorConfig(),
    )


class VideoProviderRouterTest(unittest.TestCase):
    def test_resolve_order_by_platform(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            router = VideoProviderRouter(_config(tmp))
            self.assertEqual(
                router.resolve_order("https://www.bilibili.com/video/BV1xx411c7mu", "bilibili"),
                ["bilibili_mcp", "vidscribe", "local"],
            )
            self.assertEqual(
                router.resolve_order("https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7", "xiaohongshu"),
                ["xiaohongshu_mcp", "local"],
            )
            self.assertEqual(
                router.resolve_order("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
                ["youtube_transcript_mcp", "vidscribe", "local"],
            )

    def test_route_prefers_provider_that_supplies_primary_speech(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(tmp)
            cfg.video_provider_routing.vidscribe.enabled = True
            cfg.video_provider_routing.vidscribe.command = "vidscribe"
            router = VideoProviderRouter(cfg)
            metadata_only = VideoProviderBundle(
                provider="bilibili_mcp",
                analysis_text="标题和简介",
                capabilities={
                    "has_subtitle": False,
                    "has_transcript": False,
                    "has_timed_segments": False,
                    "has_viewer_feedback": False,
                },
            )
            speech_bundle = VideoProviderBundle(
                provider="vidscribe",
                transcript_text="[00:01] 开场\n[00:10] 重点内容",
                segments=[
                    {"start": 1.0, "end": 4.0, "text": "开场"},
                    {"start": 10.0, "end": 16.0, "text": "重点内容"},
                ],
                capabilities={
                    "has_subtitle": False,
                    "has_transcript": True,
                    "has_timed_segments": True,
                    "has_viewer_feedback": False,
                },
            )
            with patch.object(router, "_from_bilibili_mcp", return_value=metadata_only), patch.object(
                router,
                "_from_command_provider",
                return_value=speech_bundle,
            ):
                bundle, attempts = router.route(
                    url="https://www.bilibili.com/video/BV1ggpcevEgk",
                    platform_hint="bilibili",
                    requested_output_lang="zh-CN",
                )
            self.assertIsNotNone(bundle)
            assert bundle is not None
            self.assertEqual(bundle.provider, "vidscribe")
            self.assertTrue(bundle.has_speech_track())
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[0].status, "ok")
            self.assertEqual(attempts[0].message, "metadata_only")
            self.assertEqual(attempts[1].status, "ok")
            self.assertEqual(attempts[1].message, "speech")


if __name__ == "__main__":
    unittest.main()
