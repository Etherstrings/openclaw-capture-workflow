"""Configuration loading."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re


@dataclass
class ObsidianConfig:
    vault_path: str
    inbox_root: str
    topics_root: str
    entities_root: str
    auto_topic_whitelist: list[str]
    auto_topic_blocklist: list[str]
    auto_entity_pages: bool = False


@dataclass
class TelegramConfig:
    result_bot_token: str


@dataclass
class SummarizerConfig:
    api_base_url: str
    api_key: str
    model: str
    timeout_seconds: int


@dataclass
class ExtractorConfig:
    webpage_text_command: str = ""
    github_text_command: str = ""
    image_ocr_command: str = ""
    video_subtitle_command: str = ""
    video_audio_command: str = ""
    video_keyframes_command: str = ""
    browser_ocr_hosts: list[str] = field(
        default_factory=lambda: ["xiaohongshu.com", "weixin.qq.com", "mp.weixin.qq.com"]
    )
    browser_ocr_min_chars_url: int = 280
    browser_ocr_min_chars_mixed: int = 900


@dataclass
class EvidenceGateConfig:
    min_chars_signal_rich_short: int = 40
    min_chars_media: int = 60
    min_chars_general: int = 80
    allow_pasted_text_without_min: bool = True


@dataclass
class RoutingConfig:
    prefer_local_extraction: bool = True
    enable_network_search_fallback: bool = False
    search_mode: str = "surfing"
    max_search_calls: int = 1
    trigger_on_partial_coverage: bool = True
    trigger_on_missing_signals: bool = True


@dataclass
class ExecutionConfig:
    dry_run_skip_model_call: bool = True
    enable_summary_cache: bool = True
    summary_cache_ttl_hours: int = 72
    cache_for_dry_run: bool = True
    cache_for_non_dry_run: bool = True
    dry_run_video_probe_seconds: int = 90
    dry_run_skip_video_audio: bool = True
    dry_run_skip_video_keyframes: bool = True


@dataclass
class AnalysisConfig:
    model: str = "gpt-5-mini"
    fallback_model: str = "gpt-5.4"
    browser_backend: str = "playwright"
    page_timeout_seconds: int = 30
    max_images: int = 6
    max_videos: int = 3
    max_tables: int = 6
    max_video_frames: int = 8
    temp_root: str = "tmp"
    pinchtab_base_url: str = ""


@dataclass
class VideoSummaryConfig:
    provider: str = "aihubmix_gemini"
    transport: str = "openai_compat"
    api_base_url: str = ""
    api_key: str = ""
    model: str = "gemini-2.5-pro"
    fallback_model: str = "gemini-2.5-flash"
    timeout_seconds: int = 60


@dataclass
class SummaryRoutingConfig:
    enabled: bool = False
    upgrade_model: str = "gpt-4.1"
    trigger_on_error: bool = True
    trigger_on_low_quality: bool = True
    low_quality_threshold: float = 0.72
    min_signal_coverage: float = 0.60
    apply_on_dry_run: bool = False


@dataclass
class VideoAccuracyConfig:
    enabled: bool = True
    min_text_chars: int = 180
    require_speech_track: bool = True
    require_visual_track: bool = False
    max_evidence_chars: int = 12000
    max_evidence_lines: int = 220
    retry_on_incomplete: bool = True
    retry_force_audio: bool = True
    retry_min_char_gain: int = 120
    budget_rmb: float = 0.5
    usd_cny: float = 7.2
    asr_usd_per_min: float = 0.006
    summary_input_usd_per_million: float = 0.15
    summary_output_usd_per_million: float = 0.60
    expected_summary_output_tokens: int = 900
    default_duration_minutes: float = 10.0
    audio_when_subtitle_short_chars: int = 280
    always_run_audio: bool = False


@dataclass
class AppConfig:
    listen_host: str
    listen_port: int
    state_dir: str
    obsidian: ObsidianConfig
    telegram: TelegramConfig
    summarizer: SummarizerConfig
    extractors: ExtractorConfig
    video_accuracy: VideoAccuracyConfig = field(default_factory=VideoAccuracyConfig)
    evidence_gate: EvidenceGateConfig = field(default_factory=EvidenceGateConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    video_summary: VideoSummaryConfig = field(default_factory=VideoSummaryConfig)
    summary_routing: SummaryRoutingConfig = field(default_factory=SummaryRoutingConfig)

    @classmethod
    def load(cls, path: str) -> "AppConfig":
        config_path = Path(path).resolve()
        _load_dotenv_file(config_path.parent / ".env")
        with open(config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        data = _resolve_env_placeholders(data)
        obsidian = dict(data["obsidian"])
        obsidian.setdefault("auto_topic_whitelist", ["AI", "股票", "GitHub", "产品", "工具", "商业"])
        obsidian.setdefault(
            "auto_topic_blocklist",
            ["测试", "总结", "结构", "回群", "回执", "路径", "显示", "验证", "本地链接", "wiki", "md", "Telegram", "Obsidian", "OpenClaw"],
        )
        obsidian.setdefault("auto_entity_pages", False)
        video_summary = VideoSummaryConfig(**data.get("video_summary", {}))
        if not video_summary.api_base_url.strip():
            video_summary.api_base_url = data.get("summarizer", {}).get("api_base_url", "https://aihubmix.com/v1")
        if not video_summary.api_key.strip():
            video_summary.api_key = data.get("summarizer", {}).get("api_key", "")
        if not video_summary.timeout_seconds:
            video_summary.timeout_seconds = int(data.get("summarizer", {}).get("timeout_seconds", 60))

        return cls(
            listen_host=data.get("listen_host", "127.0.0.1"),
            listen_port=int(data.get("listen_port", 8765)),
            state_dir=data.get("state_dir", "./state"),
            obsidian=ObsidianConfig(**obsidian),
            telegram=TelegramConfig(**data["telegram"]),
            summarizer=SummarizerConfig(**data["summarizer"]),
            extractors=ExtractorConfig(**data.get("extractors", {})),
            video_accuracy=VideoAccuracyConfig(**data.get("video_accuracy", {})),
            evidence_gate=EvidenceGateConfig(**data.get("evidence_gate", {})),
            routing=RoutingConfig(**data.get("routing", {})),
            execution=ExecutionConfig(**data.get("execution", {})),
            analysis=AnalysisConfig(**data.get("analysis", {})),
            video_summary=video_summary,
            summary_routing=SummaryRoutingConfig(**data.get("summary_routing", {})),
        )

    def ensure_state_dirs(self, base_path: Path) -> Path:
        state_dir = (base_path / self.state_dir).resolve()
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "jobs").mkdir(parents=True, exist_ok=True)
        (state_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        return state_dir

    @property
    def local_base_url(self) -> str:
        host = self.listen_host
        if host == "0.0.0.0":
            host = "127.0.0.1"
        return f"http://{host}:{self.listen_port}"


_ENV_PLACEHOLDER_RE = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


def _load_dotenv_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if not key:
            continue
        os.environ.setdefault(key, value)


def _resolve_env_placeholders(value):
    if isinstance(value, dict):
        return {key: _resolve_env_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env_placeholders(item) for item in value]
    if isinstance(value, str):
        match = _ENV_PLACEHOLDER_RE.match(value.strip())
        if not match:
            return value
        env_name = match.group(1)
        env_value = os.getenv(env_name)
        if env_value is None:
            raise ValueError(f"missing required env var: {env_name}")
        return env_value
    return value
