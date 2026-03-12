# Technical Handoff

## What This Project Is

`openclaw_capture_workflow` is the local processing backend behind an OpenClaw robot workflow.

User-facing entry is not a local CLI command. The real entry is:

1. User sends a message to the OpenClaw robot.
2. The robot is mentioned in a group chat or receives a direct message.
3. The `knowledge-capture` skill normalizes the message into a JSON payload.
4. The payload is sent to local `POST /ingest`.
5. The local workflow extracts evidence, summarizes it, writes an Obsidian note, and optionally sends a Telegram result message.

The internal protocol is defined in:

- [`openclaw-skill/knowledge-capture/SKILL.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/openclaw-skill/knowledge-capture/SKILL.md)
- [`src/openclaw_capture_workflow/server.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/src/openclaw_capture_workflow/server.py)

## Stable Entry Payload

The robot skill normalizes messages into these fields and this contract should stay stable:

- `chat_id`
- `reply_to_message_id`
- `request_id`
- `source_kind`
- `source_url`
- `raw_text`
- `image_refs`
- `platform_hint`
- `requested_output_lang`

`source_kind` currently supports:

- `pasted_text`
- `image`
- `video_url`
- `url`
- `mixed`

Important rule: `mixed` keeps all available user inputs. Do not drop text because a URL exists. Do not drop images because text exists.

## Current Routing Logic

Routing happens in two layers:

1. Robot skill maps user message to `source_kind` and `platform_hint`.
2. Local extractor dispatches by `source_kind/platform_hint`.

Current extractor dispatch:

- `pasted_text -> _from_text`
- `image -> _from_image`
- `video_url -> _from_video`
- `platform_hint=github` or GitHub URL -> `_from_github`
- `url/mixed -> _from_web`

Main implementation:

- [`src/openclaw_capture_workflow/extractor.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/src/openclaw_capture_workflow/extractor.py)

## Current Quality Strategy

The project now treats the local workflow as the main processor, not the Skill itself.

Recent changes added:

- Content profiles: `skill_recommendation`, `installation_tutorial`, `project_overview`, `video_explainer`, `general_capture`
- Structured signal requirements per profile
- Stronger summary validation: missing key repo links / skill ids / commands now count as invalid low-quality output
- Richer `/jobs/<id>` observability: `entry_context`, `content_profile`, `signal_requirements`, `evidence_sources`, `summary_quality`
- New first-screen Obsidian layout:
  - `一句话总结`
  - `项目与链接`
  - `核心事实`
  - `执行清单`
  - `关键证据`
  - `专业解读`

Core files:

- [`src/openclaw_capture_workflow/content_profile.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/src/openclaw_capture_workflow/content_profile.py)
- [`src/openclaw_capture_workflow/processor.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/src/openclaw_capture_workflow/processor.py)
- [`src/openclaw_capture_workflow/summarizer.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/src/openclaw_capture_workflow/summarizer.py)
- [`src/openclaw_capture_workflow/obsidian.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/src/openclaw_capture_workflow/obsidian.py)

## Fixed Regression Assets

These files are the main continuation points for future sessions:

- Fixed robot payload regression set:
  - [`scripts/robot_ingest_regression_cases.json`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/scripts/robot_ingest_regression_cases.json)
- Payload replay helper:
  - [`scripts/run_robot_payload_replay.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/scripts/run_robot_payload_replay.py)
- Robot replay tests:
  - [`tests/test_robot_entry_replay.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/tests/test_robot_entry_replay.py)

## How To Resume Quickly In A New Session

Read these in order:

1. [`README.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/README.md)
2. [`TASK_STATUS.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/TASK_STATUS.md)
3. [`docs/TECHNICAL_HANDOFF.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/docs/TECHNICAL_HANDOFF.md)
4. [`docs/ACCURACY_BASELINE.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/docs/ACCURACY_BASELINE.md)

Then run:

```bash
python3 -m unittest discover -s tests
python3 scripts/run_robot_payload_replay.py --limit 2
```

## Secrets And Publishing Rules

Local-only files that must never be pushed:

- `config.json`
- `.env`
- `state/`
- `.venv/`

Public repo should only contain:

- source code
- tests
- scripts
- `config.example.json`
- `.env.example`
- docs

## Known Safe Cleanup Scope In Obsidian

OpenClaw-managed notes live under:

- `/Users/boyuewu/Documents/Obsidian Vault/Inbox/OpenClaw`

Safe-to-clean first:

- `Inbox/OpenClaw/Compare`
- `Inbox/OpenClaw/Diagnostics`
- duplicate notes detected by [`scripts/cleanup_obsidian.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/scripts/cleanup_obsidian.py)

Primary inbox notes under `Inbox/OpenClaw/YYYY/MM` should be treated as user data unless explicitly archived.
