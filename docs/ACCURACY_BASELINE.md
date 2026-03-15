# Accuracy Baseline

## Current Baseline

As of 2026-03-12, the local workflow has these verified baselines:

- Unit/integration tests: `92 passed`
- Fixed robot replay tests cover:
  - group-chat mention entry
  - direct-message entry
  - mixed payload retention
  - payload-to-`IngestRequest` roundtrip

Main test command:

```bash
python3 -m unittest discover -s tests
```

## Why Native Local Processing Won

This project previously tested whether OpenClaw Skill-side summarization could replace local processing.

The historical conclusion was: no.

The `native vs skill` reports showed that the local native pipeline was materially better at keeping:

- repo links
- skill ids
- install actions
- action checklist content
- high-signal first-screen structure

Key historical report:

- [`state/reports/compare_native_vs_skill_20260312_003628_side_by_side.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/reports/compare_native_vs_skill_20260312_003628_side_by_side.md)

This is why the current architecture treats:

- OpenClaw robot + skill = message entry layer
- local workflow = real processing layer

## Fixed Regression Sets

### 1. Robot entry regression set

Primary fixed set for future development:

- [`scripts/robot_ingest_regression_cases.json`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/scripts/robot_ingest_regression_cases.json)

Coverage includes:

- group-chat mention cases
- direct-message cases
- pure URL
- pure pasted text
- mixed text + image + URL
- GitHub repo
- video URLs

### 2. Historical evaluation sets

- Multi-type regression:
  - [`scripts/accuracy_eval_cases.multitype.json`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/scripts/accuracy_eval_cases.multitype.json)
- New video samples:
  - [`scripts/accuracy_eval_cases.new_videos.json`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/scripts/accuracy_eval_cases.new_videos.json)
- XHS-specific cases:
  - [`scripts/accuracy_eval_cases.xhs.json`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/scripts/accuracy_eval_cases.xhs.json)

## What The Latest Changes Enforce

Recent accuracy-focused changes added hard requirements by content type.

### Skill recommendation

Expected to keep:

- skill name
- skill id
- project/repo
- source link
- install method
- usable follow-up actions

### Installation tutorial

Expected to keep:

- prerequisites when present
- key commands
- validation action
- action checklist

### Project overview

Expected to keep:

- project name
- repo/doc link
- core purpose
- usage boundary when evidence provides it

### Video explainer

Expected behavior:

- explicit downgrade when speech/key evidence is incomplete
- no fake “full understanding” from weak page text alone

## Main Quality Signals In `/jobs/<id>`

Useful fields to inspect after a run:

- `result.entry_context`
- `result.content_profile`
- `result.signal_requirements`
- `result.evidence_sources`
- `result.summary_quality`
- `warnings`
- `phase_status`

These fields help distinguish:

- bad robot payload
- weak extraction
- weak summary
- weak note rendering

## Current Strong Areas

- GitHub repo / markdown / structured text capture
- payload normalization and routing visibility
- dry-run preview workflow
- note structure is much more front-loaded than the old layout
- macOS 26+ local Apple ASR can now provide video speech evidence without requiring an external STT key

## Current Weak Areas

- XHS dynamic extraction still depends heavily on browser-readable evidence
- some skill/install notes still produce verbose skill naming instead of perfectly normalized short names
- video quality still depends on subtitle/ASR/keyframe availability and external platform restrictions
- Apple local ASR is machine-dependent and currently only available on macOS 26+ with `swift` + `SpeechTranscriber`
- real robot end-to-end validation is not yet re-run after the newest structure changes

## Recommended Validation Order For Future Work

1. `python3 -m unittest discover -s tests`
2. `python3 scripts/run_robot_payload_replay.py --limit 2`
3. Re-run one high-value skill/install case
4. Re-run one XHS case
5. Re-run one video case only if extraction changed

## Acceptance Checklist

A run is “good enough” only if:

- repo/source link is present
- skill id is present when evidence has one
- install command/action is present when evidence has one
- first screen is readable without scrolling through images or debug blocks
- `/jobs/<id>` clearly shows where evidence came from
- weak video evidence is marked partial or refused
