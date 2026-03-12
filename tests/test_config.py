import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.config import AppConfig


def _base_config_dict(tmp: str, token_var: str = "RESULT_BOT_TOKEN", key_var: str = "SUMMARIZER_API_KEY") -> dict:
    return {
        "listen_host": "127.0.0.1",
        "listen_port": 8765,
        "state_dir": "./state",
        "obsidian": {
            "vault_path": tmp,
            "inbox_root": "Inbox/OpenClaw",
            "topics_root": "Topics",
            "entities_root": "Entities",
        },
        "telegram": {"result_bot_token": "${" + token_var + "}"},
        "summarizer": {
            "api_base_url": "https://aihubmix.com/v1",
            "api_key": "${" + key_var + "}",
            "model": "gpt-4o-mini",
            "timeout_seconds": 60,
        },
    }


class ConfigLoadTest(unittest.TestCase):
    def test_load_resolves_env_placeholders_from_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "RESULT_BOT_TOKEN=token_from_env",
                        "SUMMARIZER_API_KEY=sk_from_env",
                    ]
                ),
                encoding="utf-8",
            )
            config_path = root / "config.json"
            config_path.write_text(json.dumps(_base_config_dict(tmp), ensure_ascii=False), encoding="utf-8")
            cfg = AppConfig.load(str(config_path))
            self.assertEqual(cfg.telegram.result_bot_token, "token_from_env")
            self.assertEqual(cfg.summarizer.api_key, "sk_from_env")

    def test_load_raises_on_missing_required_env_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_var = "MISSING_RESULT_BOT_TOKEN_FOR_TEST"
            key_var = "MISSING_SUMMARIZER_API_KEY_FOR_TEST"
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(_base_config_dict(tmp, token_var=token_var, key_var=key_var), ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                AppConfig.load(str(config_path))

    def test_load_execution_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "RESULT_BOT_TOKEN=token_from_env",
                        "SUMMARIZER_API_KEY=sk_from_env",
                    ]
                ),
                encoding="utf-8",
            )
            config_path = root / "config.json"
            config_path.write_text(json.dumps(_base_config_dict(tmp), ensure_ascii=False), encoding="utf-8")
            cfg = AppConfig.load(str(config_path))
            self.assertTrue(cfg.execution.dry_run_skip_model_call)
            self.assertTrue(cfg.execution.enable_summary_cache)
            self.assertEqual(cfg.execution.summary_cache_ttl_hours, 72)
            self.assertEqual(cfg.execution.dry_run_video_probe_seconds, 90)
            self.assertTrue(cfg.execution.dry_run_skip_video_audio)
            self.assertTrue(cfg.execution.dry_run_skip_video_keyframes)
            self.assertFalse(cfg.summary_routing.enabled)
            self.assertEqual(cfg.summary_routing.upgrade_model, "gpt-4.1")


if __name__ == "__main__":
    unittest.main()
