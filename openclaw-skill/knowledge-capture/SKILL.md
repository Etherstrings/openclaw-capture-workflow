---
name: knowledge-capture
description: Capture links, text, images, and GitHub pages into a local workflow that writes Obsidian notes and sends a Telegram result summary through bot B.
---

# Knowledge Capture

Use this skill when the user wants to archive a link, pasted text, image, or GitHub page into Obsidian and receive a structured result back in Telegram.

## Behavior

1. Normalize the request into a JSON payload with:
   - `chat_id`
   - `reply_to_message_id`
   - `request_id`
   - `source_kind`
   - `source_url`
   - `raw_text`
   - `image_refs`
   - `platform_hint`
   - `requested_output_lang`
2. Immediately tell the user: `已收到，开始处理。`
3. Send the payload to the local workflow:

```bash
curl -sS -X POST http://127.0.0.1:8765/ingest \
  -H 'Content-Type: application/json' \
  -d '<payload-json>'
```

## Source kind rules

- `pasted_text`: user pasted plain text
- `image`: user sent screenshot or image
- `video_url`: Bilibili, Xiaohongshu, YouTube, or other video link
- `url`: general web article or public page
- `mixed`: text with one or more links, or text + images

For `mixed`, keep **all** available inputs in one payload:

- put the main link in `source_url` (if present)
- put pasted long text in `raw_text`
- put uploaded/local images in `image_refs`
- keep `platform_hint` when obvious (`github`, `xiaohongshu`, `bilibili`, etc.)

Do not drop user text when a URL is present. Do not drop images when text is present.

## GitHub handling

If the URL is a GitHub repo, README, issue, PR, commit, release, or file, set:

- `platform_hint=github`
- `source_kind=url`

If GitHub URL comes with additional pasted analysis text or screenshots, set:

- `platform_hint=github`
- `source_kind=mixed`

## Output expectation

The local workflow will:

- build a conservative summary from extracted evidence
- write one main note to Obsidian
- update topic and entity links
- send a result message through Telegram bot B

Do not try to manually summarize in chat after the workflow has accepted the task unless the user asks for an immediate inline summary.
