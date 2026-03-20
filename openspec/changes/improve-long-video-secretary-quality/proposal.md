## Why

The project can now extract meaningful evidence from many videos, but long-video output still degrades into timeline dumps, raw ASR fragments, or generic fallback wording. This is most visible in long Bilibili runs, where model-specific failures and chunk-based fallbacks prevent the system from clearly reconstructing what the video is actually saying.

## What Changes

- Introduce a long-video understanding contract that separates evidence synthesis from final summary rendering.
- Reshape long-video output around clear content reconstruction: what the video is about, its main thesis, major sections, key verified points, and explicit unknowns.
- Add a specific degradation contract for usable partial coverage, model unavailable, blocked capture, and refusal so each user-visible summary matches what is actually known.
- Reduce dependence on a single video-only final rendering path by adding a provider-agnostic final summary fallback.
- Add regression validation for long Bilibili and Xiaohongshu samples so video-understanding quality is checked beyond extraction success.

## Capabilities

### New Capabilities
- `video-understanding-summary`: Define the user-facing contract for long-video summaries, previews, and replies so they clearly reconstruct the video's meaning instead of dumping chunks.
- `video-summary-degradation`: Define explicit degradation states and fallback behavior for partial coverage, model failure, blocked capture, and refusal.

### Modified Capabilities
- None.

## Impact

- Affected code: `src/openclaw_capture_workflow/video_experiment_summarizer.py`, `src/openclaw_capture_workflow/processor.py`, `src/openclaw_capture_workflow/telegram.py`, `src/openclaw_capture_workflow/obsidian.py`, and related render/quality helpers.
- Affected systems: long-video dry-run previews, persisted Obsidian notes, Telegram replies, and `/jobs/<id>` observability.
- Validation impact: new regression fixtures and quality assertions for long-video Bilibili/XHS runs.
