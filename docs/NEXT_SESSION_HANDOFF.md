# Next Session Handoff

Last updated: 2026-03-15

## Current Goal

This project is no longer trying to prove that a Skill alone can solve the problem.

The real goal is:

- OpenClaw robot = message entry
- local workflow = real processor
- output should feel like a busy-user private secretary:
  - fast to scan
  - clear about usefulness
  - honest about confidence
  - strong enough to archive into Obsidian

The user's explicit requirement is:

- daytime use
- minimal attention cost
- answer should quickly tell:
  - what this content is
  - whether it is worth reading/watching later
  - what is useful to the user
- final note should include:
  - text mind map near the top
  - bold keyword block near the end
  - notes should link to each other inside Obsidian

## What Was Done In This Session

### 1. Robot-entry and regression structure

Implemented and verified:

- fixed robot payload regression set
- payload replay helper
- robot replay tests for:
  - group mention entry
  - direct-message entry
  - mixed payload retention

Files:

- [`scripts/robot_ingest_regression_cases.json`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/scripts/robot_ingest_regression_cases.json)
- [`scripts/run_robot_payload_replay.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/scripts/run_robot_payload_replay.py)
- [`tests/test_robot_entry_replay.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/tests/test_robot_entry_replay.py)

### 2. Stronger content understanding metadata

Implemented:

- content profiles:
  - `skill_recommendation`
  - `installation_tutorial`
  - `project_overview`
  - `video_explainer`
  - `general_capture`
- signal requirements per profile
- richer `/jobs/<id>` result metadata:
  - `entry_context`
  - `content_profile`
  - `signal_requirements`
  - `evidence_sources`
  - `summary_quality`
  - `video_assessment`

Files:

- [`src/openclaw_capture_workflow/content_profile.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/src/openclaw_capture_workflow/content_profile.py)
- [`src/openclaw_capture_workflow/processor.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/src/openclaw_capture_workflow/processor.py)
- [`src/openclaw_capture_workflow/summarizer.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/src/openclaw_capture_workflow/summarizer.py)

### 3. Obsidian output reshaped toward “secretary mode”

Implemented:

- frontloaded note structure
- text mind map
- usefulness section (`对你有什么用`)
- evidence section
- confidence/limitations section
- bold keyword section

Current note structure:

1. `一句话总结`
2. `文字脑图`
3. `对你有什么用`
4. `项目与链接`
5. `关联笔记`
6. `核心事实`
7. `执行清单`
8. `关键证据`
9. `专业解读`
10. `可信度与局限`
11. `来源`
12. `关键词`

Files:

- [`src/openclaw_capture_workflow/obsidian.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/src/openclaw_capture_workflow/obsidian.py)
- [`src/openclaw_capture_workflow/telegram.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/src/openclaw_capture_workflow/telegram.py)

### 4. Video extraction actually improved

Critical discovery:

- previous poor results were not only “model quality”
- many weak results happened because:
  - Bilibili videos had no official subtitle
  - dry-run skipped audio extraction by default
  - Xiaohongshu audio downloads were saved as `audio.mp4` but the script rejected them as “not audio”

Fixes implemented:

- Bilibili dry-run can now use:
  - platform metadata
  - AIHub ASR
  - keyframes
  - keyframe OCR
- Xiaohongshu dry-run can now use:
  - AIHub ASR
  - keyframes
  - keyframe OCR
- video audio picker now accepts container files such as:
  - `mp4`
  - `mov`
  - `mkv`
  - `m4v`
- dry-run quality path no longer self-sabotages by always skipping audio/keyframes for Bilibili/XHS
- Bilibili metadata was added:
  - title
  - description
  - tags
  - uploader
  - play/like counts

Files:

- [`scripts/video_audio_asr.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/scripts/video_audio_asr.py)
- [`scripts/video_keyframes_extract.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/scripts/video_keyframes_extract.py)
- [`scripts/video_subtitle_extract.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/scripts/video_subtitle_extract.py)
- [`src/openclaw_capture_workflow/extractor.py`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/src/openclaw_capture_workflow/extractor.py)

## Proven Facts From Real Runs

### AIHub ASR support

Confirmed:

- AIHub ASR works with current OpenAI-compatible `/audio/transcriptions` path
- no SiliconFlow switch is required just to make ASR functional
- macOS 26+ now has a second path: local Apple `SpeechTranscriber` can be used before remote STT

Current status:

- AIHub ASR = working
- Apple local ASR = working on this machine and can run without `STT_API_KEY`
- remote OCR API = not yet integrated
- local OCR is still used for image/keyframe OCR unless replaced by `image_ocr_command`

### Bilibili results

#### Strong short/medium sample

Current strong sample:

- URL: `https://www.bilibili.com/video/BV1tyNNzxEpK`

This sample now reaches:

- `video_assessment.level = strong`
- `coverage = full`
- evidence includes real ASR body text

Recent preview examples:

- [`state/previews/report-bili-1773320241.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/previews/report-bili-1773320241.md)
- [`state/previews/secretary-bili-1773320827.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/previews/secretary-bili-1773320827.md)

#### New long sample (>10 min)

Validated long sample:

- URL: `https://www.bilibili.com/video/BV1y4411p74E`
- duration: `2597s` (~43m17s)

Result:

- content is clearly being read from the video body, not just page shell
- but report organization is still weak

Latest preview:

- [`state/previews/sample-bili-long-1773325820.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/previews/sample-bili-long-1773325820.md)

Known problems in this long Bilibili sample:

- conclusion too weak (`已提取核心事实`)
- incorrect duration phrasing in summary
- polluted action checklist
- still not “professional secretary report” quality

### Xiaohongshu results

#### Strong working sample

Current strong sample:

- URL: `https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7`

This sample now reaches:

- `video_assessment.level = strong`
- `coverage = full`
- evidence includes:
  - `video_audio_asr`
  - `video_keyframes`
  - `video_keyframe_ocr`

Recent preview examples:

- [`state/previews/report-xhs-1773320241.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/previews/report-xhs-1773320241.md)
- [`state/previews/secretary-xhs-1773320827.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/previews/secretary-xhs-1773320827.md)

#### New >5 minute sample

Validated long-enough sample:

- URL: `https://www.xiaohongshu.com/explore/69afbf57000000001d027f5a`
- title: `面试完说等通知，到底在等什么通知`
- duration: `336.434s` (~5m36s)

Result:

- much better than the earlier weak XHS page-only outputs
- already fairly usable as a secretary-style note

Latest preview:

- [`state/previews/sample-xhs-long-1773325820.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/previews/sample-xhs-long-1773325820.md)

## Obsidian State

The user asked for OpenClaw notes to be cleaned.

Current Obsidian state:

- only Showcase notes remain under:
  - `/Users/boyuewu/Documents/Obsidian Vault/Inbox/OpenClaw/Showcase`
- old notes were moved into backup:
  - `/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/obsidian_cleanup_backup_20260312_191641`

Current Showcase files:

- [`00_showcase_index.md`](/Users/boyuewu/Documents/Obsidian%20Vault/Inbox/OpenClaw/Showcase/00_showcase_index.md)
- [`01_skill_install_note.md`](/Users/boyuewu/Documents/Obsidian%20Vault/Inbox/OpenClaw/Showcase/01_skill_install_note.md)
- [`02_github_project_note.md`](/Users/boyuewu/Documents/Obsidian%20Vault/Inbox/OpenClaw/Showcase/02_github_project_note.md)
- [`03_bilibili_video_report.md`](/Users/boyuewu/Documents/Obsidian%20Vault/Inbox/OpenClaw/Showcase/03_bilibili_video_report.md)
- [`04_xhs_video_report.md`](/Users/boyuewu/Documents/Obsidian%20Vault/Inbox/OpenClaw/Showcase/04_xhs_video_report.md)

## Public Repo

Public GitHub repo was created:

- [Etherstrings/openclaw-capture-workflow](https://github.com/Etherstrings/openclaw-capture-workflow)

Repo commit at creation:

- `11cac6e81a3b650ee564a41ffa87b6a142922cc0`

## Current Test Status

Current verified test count:

- `101 passed`

Main command:

```bash
python3 -m unittest discover -s tests
```

## What Is Still Not Done

### 1. Long-video report quality is still not good enough

This is the main remaining problem.

Shorter strong samples are now readable.
Longer videos can be read, but report organization is still weaker than desired.

Main gap:

- it reads content, but does not yet think like a strong executive secretary

Needed improvements:

- stronger “what changed / what matters / what to do next” framing
- better timeline segmentation
- more useful action items
- less generic conclusion phrasing

### 2. Classification path tags are still too weak

User explicitly asked for easy hierarchical categorization like:

- `娱乐`
- `游戏`
- `杀戮尖塔2`

Current output has:

- `primary_topic`
- `secondary_topics`
- bold keyword block

But there is **no explicit hierarchical classification path** yet.

This still needs to be implemented.

### 3. Browser cleanup after extraction is still not implemented

User explicitly wants:

- if browser/video tab was opened for extraction:
  - pause video after read
  - or close temporary tab

Current workflow still does not guarantee this cleanup behavior.

This is still an open UX issue.

### 4. Remote OCR API is still not integrated

Current situation:

- ASR path = working
- remote OCR path = not yet integrated

If better OCR quality is needed, next session can wire:

- `extractors.image_ocr_command`

to a stronger remote OCR model/service.

### 5. Real robot end-to-end validation still not re-run

Robot payload normalization and local replay are tested.
But real:

- group mention -> robot -> local workflow -> Telegram reply
- direct-message -> robot -> local workflow -> Telegram reply

have not been re-run after the newest video/secretary-style changes.

## Immediate Recommended Next Steps

Start here next session:

1. Fix long Bilibili report quality
   - use current long sample:
     - `https://www.bilibili.com/video/BV1y4411p74E`
2. Add explicit hierarchical classification path
   - example target output:
     - `娱乐 / 游戏 / 杀戮尖塔2`
     - `职业发展 / 求职 / 面试流程`
3. Add browser post-read cleanup
   - pause video if still playing
   - close temporary extraction tabs
4. Only after that:
   - consider stronger OCR integration
   - consider SiliconFlow ASR as optional secondary provider

## Short Honest Status Summary

What is genuinely solved now:

- AIHub ASR works
- Bilibili and Xiaohongshu video body can now be read
- secretary-style note scaffolding exists
- Obsidian is cleaned and showcase notes are linked

What is not solved yet:

- long video output still feels too dumb / too summary-like
- classification path not implemented
- browser cleanup UX not implemented
- remote OCR not wired

## If Starting Fresh Next Time

Read in this order:

1. [`docs/NEXT_SESSION_HANDOFF.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/docs/NEXT_SESSION_HANDOFF.md)
2. [`docs/TECHNICAL_HANDOFF.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/docs/TECHNICAL_HANDOFF.md)
3. [`docs/ACCURACY_BASELINE.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/docs/ACCURACY_BASELINE.md)
4. Open the two sample previews:
   - [`state/previews/sample-bili-long-1773325820.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/previews/sample-bili-long-1773325820.md)
   - [`state/previews/sample-xhs-long-1773325820.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/previews/sample-xhs-long-1773325820.md)
