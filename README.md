# OpenClaw Capture Workflow

Local workflow service for `OpenClaw` that:

- receives normalized ingestion payloads
- extracts or accepts evidence text
- generates a conservative summary with an OpenAI-compatible API
- writes a main note plus topic/entity links into an Obsidian vault
- sends a result message through a Telegram bot token

## What this project includes

- A local HTTP service with `POST /ingest`, `GET /health`, and `GET /jobs/<id>`
- A background worker with JSON-file job storage
- A configurable extractor adapter layer for text, image OCR, subtitles, audio, and keyframes
- A conservative summarizer client for OpenAI-compatible chat endpoints
- Obsidian note generation with:
  - one main note in `Inbox/OpenClaw/YYYY/MM/`
  - topic index pages
  - entity pages
  - ASCII structure map at the top
- An OpenClaw skill template plus install script

## Quick start

1. Copy the example config and adjust paths and tokens:

```bash
cp config.example.json config.json
cp .env.example .env
```

2. Install local media dependencies (required for high-quality video extraction):

```bash
brew install yt-dlp ffmpeg
```

If you cannot install system binaries, the bundled scripts can fall back to Python packages (`yt-dlp`, `imageio-ffmpeg`).

3. Start the local service:

```bash
PYTHONPATH=src python3 -m openclaw_capture_workflow.cli serve --config config.json
```

4. Install the bundled OpenClaw skill into your local state:

```bash
bash scripts/install_skill.sh
```

5. Ask OpenClaw to use the `knowledge-capture` skill when you want a link, text, image, or video archived into Obsidian.

## Config notes

- `obsidian.vault_path` must point to your local vault.
- `telegram.result_bot_token` should be bot B's token (supports `${ENV_VAR}` placeholders in `config.json`).
- `summarizer.api_base_url` can target any OpenAI-compatible endpoint, including AIHubMix or OpenAI direct.
- `config.json` supports `${ENV_VAR}` placeholders. The loader auto-reads `.env` in the same folder.
- `extractors.*_command` are optional shell command templates. Use `{url}`, `{input_path}`, `{output_path}`, `{max_seconds}`, `{api_key}`, `{api_base_url}` placeholders.
- The default `config.example.json` wires three local scripts for video:
  - `scripts/video_subtitle_extract.py` (subtitle-first)
  - `scripts/video_audio_asr.py` (ASR fallback, OpenAI-compatible STT endpoint)
  - `scripts/video_keyframes_extract.py` (visual keyframes)
- For Bilibili URLs, scripts try Bilibili public APIs first (view/playurl/subtitle), then fall back to `yt-dlp`.
- For STT scripts, define `.env` values:
  - `STT_API_KEY`
  - `STT_API_BASE_URL` (for AIHubMix: `https://aihubmix.com/v1`)
  - `STT_MODEL` (default `whisper-1`)
  - `VIDEO_COOKIES_FROM_BROWSER` (for YouTube/XHS/B站 anti-bot pages, e.g. `chrome`)
  - `VIDEO_COOKIES_PATH` (optional exported cookies file path)
- `video_accuracy` controls video quality warnings and budget estimation:
  - `budget_rmb` default is `0.5` (10-minute target).
  - subtitles are used first; audio ASR runs only when subtitle text is too short (or `always_run_audio=true`).
  - missing tracks do not hard-fail by default; the job is completed with warnings for manual review.
  - each video job returns `video_cost_estimate` in `/jobs/<id>`.
- `evidence_gate` and `routing` make behavior shareable by config:
  - evidence gate thresholds (short text acceptance) are configurable.
  - network-search fallback policy is configurable without code changes.
- `execution` controls cost-sensitive behavior:
  - `dry_run_skip_model_call=true` avoids LLM calls during dry-run.
  - `enable_summary_cache=true` reuses summaries for the same source URL + evidence fingerprint.
  - `summary_cache_ttl_hours` controls cache validity window.
  - `dry_run_video_probe_seconds=90` limits dry-run video extraction to a short probe window.
  - `dry_run_skip_video_audio=true` and `dry_run_skip_video_keyframes=true` avoid expensive media tracks in dry-run.
  - extractor command templates can consume `{max_seconds}` to implement partial extraction.
- `summary_routing` controls automatic model upgrade:
  - default path can stay on `gpt-4o-mini`.
  - when enabled, low-quality outputs or model errors can auto-upgrade to `upgrade_model` (for example `gpt-4.1`).
  - quality trigger uses `low_quality_threshold` and `min_signal_coverage`.
  - `apply_on_dry_run=false` avoids extra cost during dry-run.

## Verification

Run the built-in unit tests:

```bash
python3 -m unittest discover -s tests
```

## Handoff docs

For future sessions, read:

- `docs/TECHNICAL_HANDOFF.md`
- `docs/ACCURACY_BASELINE.md`

For saved robot-entry payload replay:

```bash
python3 scripts/run_robot_payload_replay.py --limit 2
```

## Accuracy evaluation module

Use the built-in evaluator to identify exactly which step is failing (`extract`, `signals`, `summary`, `renderer`) for each real link/video case.

1. Edit or copy case definitions:

```bash
cp scripts/accuracy_eval_cases.example.json scripts/accuracy_eval_cases.local.json
```

2. Run low-cost rule evaluation (no summary model calls):

```bash
python3 scripts/run_accuracy_eval.py \
  --config config.json \
  --cases scripts/accuracy_eval_cases.local.json \
  --summary-mode fallback
```

3. Run model-backed evaluation (higher accuracy, costs money):

```bash
python3 scripts/run_accuracy_eval.py \
  --config config.json \
  --cases scripts/accuracy_eval_cases.local.json \
  --summary-mode model \
  --summary-model gpt-4.1 \
  --summary-price-in 0.15 \
  --summary-price-out 0.60
```

4. Optional: add a judge model for stricter QA scoring:

```bash
python3 scripts/run_accuracy_eval.py \
  --config config.json \
  --cases scripts/accuracy_eval_cases.local.json \
  --summary-mode model \
  --enable-judge \
  --judge-model gpt-4.1-mini
```

The tool writes:

- JSON report: `state/reports/accuracy_eval_*.json`
- Markdown report: `state/reports/accuracy_eval_*.md`
- Per-case preview notes: `state/previews/acc-*.md`

### Multi-type progressive validation

Run staged verification across mixed sources (XHS 图文/纯文字、B站视频、GitHub无README、GitHub Markdown文档、YouTube):

```bash
python3 scripts/run_progressive_validation.py \
  --config config.json \
  --cases scripts/accuracy_eval_cases.multitype.json \
  --mini-model gpt-4o-mini \
  --strong-model gpt-4.1
```

Outputs:

- stage reports: `state/reports/progressive_fallback_*.{json,md}`
- stage reports: `state/reports/progressive_mini_*.{json,md}`
- stage reports: `state/reports/progressive_strong_*.{json,md}`
- combined matrix: `state/reports/progressive_combined_*.{json,md}`
