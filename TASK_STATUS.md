# OpenClaw Capture Workflow - Task Status

Last updated: 2026-03-11

## Completed

1. Job state semantics
- Scope: split phase status (`extract/summarize/write_note/notify`), treat notify failure as warning.
- Status: done.

2. Dry-run read-only mode
- Scope: no vault write, no topic/entity mutation, no telegram send, return `note_preview`.
- Status: done.

3. Output quality baseline
- Scope: concise note template, one-line summary, project/repo first, 100-200 char explainer, URL canonicalization.
- Status: done (iterative tuning continues).

4. Evidence gate and fallback visibility
- Scope: URL-only / fetch-error evidence rejection, explicit `summary_mode` (`normal/cache/fallback/fallback_dry_run`).
- Status: done.

5. Video cost guardrails
- Scope: `video_accuracy` + `execution` config block, dry-run probe controls, summary cache.
- Status: done.

6. False positive fix (`00/01` repo)
- Scope: stronger repo candidate validation in signal extractor.
- Status: done.

7. Bilibili normalization + noise cleanup
- Scope: canonical BV URL, timeline/comment/recommendation noise filtering.
- Status: done (still improveable with better tracks).

8. Artifact cleanup behavior
- Scope: temporary screenshot/keyframe cleanup after job.
- Status: done.

9. Job store write stability
- Scope: atomic file replace in `JobStore.save` to avoid partial JSON reads.
- Status: done.

10. Shareable video extractor scripts
- Scope: subtitle-first, ASR fallback, keyframe extraction scripts + env/config examples.
- Status: done.

## In Progress

1. High-accuracy video end-to-end tuning
- Goal: NotebookLM-like quality for video link analysis.
- Current gap: quality still needs stronger "teaching-style explanation" polish on long/complex videos.

## Pending (High Priority)

1. Wire real STT path with your key in runtime config
- Owner: User + Assistant
- Done when: `video_audio_command` successfully returns transcript JSON on real Bilibili URL.
- Status: done in local validation (Bilibili sample URL), pending production bot path.

2. One paid full pass on your sample video
- Owner: Assistant
- Done when: full run (not dry-run) yields high-confidence summary and clean key facts in note + telegram.
- Status: dry-run + model-call pass done; non-dry-run bot callback path pending.

3. Telegram A->B end-to-end validation
- Owner: User + Assistant
- Done when: @A in group triggers parse and B sends final result with expected format.

4. Obsidian vault structure cleanup pass
- Owner: Assistant
- Done when: duplicate/low-value topic links removed and index relations become stable.

## Cost Control Rules (Current)

1. Dry-run skips model call by default.
2. Summary cache enabled.
3. Dry-run video uses short probe window and can skip audio/keyframes.
4. One paid run only after probe output passes quality gate.
