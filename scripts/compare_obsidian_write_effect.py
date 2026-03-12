#!/usr/bin/env python3
"""Compare native summarizer vs summarize-skill by writing two notes into Obsidian."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _trim_line(text: str, limit: int = 120) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip()).strip("。；;")
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def _skill_output_lines(raw: str) -> list[str]:
    lines: list[str] = []
    for line in (raw or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if re.match(r"^\d+s · \$[\d.]+ · \d+ words", s):
            continue
        s = re.sub(r"^[-*]\s+", "", s)
        lines.append(_trim_line(s, limit=180))
    dedup: list[str] = []
    seen: set[str] = set()
    for item in lines:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)
    return dedup


def _run_skill_summary(text: str, model: str, timeout_sec: int) -> tuple[str, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
        tmp.write(text or "")
        tmp_path = tmp.name
    cmd = [
        "summarize",
        tmp_path,
        "--length",
        "short",
        "--max-output-tokens",
        "700",
    ]
    if model.strip():
        cmd += ["--model", model.strip()]
    try:
        done = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=max(20, int(timeout_sec)),
        )
        return done.stdout.strip(), ""
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        return stdout, f"skill_cmd_failed: {stderr or stdout or str(exc)}"
    except subprocess.TimeoutExpired:
        return "", "skill_cmd_timeout"
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


def _summary_from_skill_text(skill_text: str, evidence, fallback_builder):
    base = fallback_builder(evidence)
    lines = _skill_output_lines(skill_text)
    if not lines:
        return base

    title = base.title
    first = lines[0]
    if len(first) <= 60 and not first.endswith(":"):
        title = first

    conclusion = lines[0]
    if len(conclusion) > 90:
        conclusion = conclusion[:90].rstrip() + "..."

    bullets = []
    for line in lines:
        if len(line) < 6:
            continue
        bullets.append(line)
        if len(bullets) >= 6:
            break
    if not bullets:
        bullets = base.bullets

    quotes = []
    for line in lines:
        if any(token in line.lower() for token in ["http://", "https://", "github.com"]):
            quotes.append(line)
        if len(quotes) >= 3:
            break
    if not quotes:
        quotes = base.evidence_quotes

    return base.__class__(
        title=title,
        primary_topic=base.primary_topic,
        secondary_topics=base.secondary_topics,
        entities=base.entities,
        conclusion=conclusion,
        bullets=bullets,
        evidence_quotes=quotes,
        coverage=base.coverage,
        confidence=base.confidence,
        note_tags=base.note_tags,
        follow_up_actions=base.follow_up_actions,
    )


def _write_preview_note(vault: Path, rel_path: str, content: str) -> Path:
    path = vault / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def main() -> int:
    root = _project_root()
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from openclaw_capture_workflow.config import AppConfig
    from openclaw_capture_workflow.extractor import EvidenceExtractor
    from openclaw_capture_workflow.models import IngestRequest
    from openclaw_capture_workflow.obsidian import ObsidianWriter
    from openclaw_capture_workflow.processor import _build_fallback_summary
    from openclaw_capture_workflow.summarizer import OpenAICompatibleSummarizer

    parser = argparse.ArgumentParser(description="Compare note writing effect: native vs summarize-skill")
    parser.add_argument("--config", default=str(root / "config.json"))
    parser.add_argument("--source-url", default="")
    parser.add_argument("--raw-text", default="")
    parser.add_argument("--platform-hint", default="")
    parser.add_argument("--skill-model", default="cli/claude/sonnet")
    parser.add_argument("--out-root", default="Inbox/OpenClaw/Compare")
    args = parser.parse_args()

    if not args.source_url.strip() and not args.raw_text.strip():
        raise SystemExit("Provide either --source-url or --raw-text")

    cfg = AppConfig.load(args.config)
    base_dir = Path(args.config).resolve().parent
    state_dir = cfg.ensure_state_dirs(base_dir)

    extractor = EvidenceExtractor(cfg, state_dir / "artifacts")
    writer = ObsidianWriter(cfg.obsidian)
    native = OpenAICompatibleSummarizer(cfg.summarizer)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ingest = IngestRequest(
        chat_id="-1",
        reply_to_message_id="1",
        request_id=f"cmp-note-{stamp}",
        source_kind="url" if args.source_url.strip() else "pasted_text",
        source_url=args.source_url.strip() or None,
        raw_text=args.raw_text.strip() or None,
        platform_hint=args.platform_hint.strip() or None,
        dry_run=True,
    )
    evidence = extractor.extract(ingest)

    native_error = ""
    try:
        native_summary = native.summarize(evidence)
    except Exception as exc:
        native_error = str(exc)
        native_summary = _build_fallback_summary(evidence)

    skill_raw, skill_error = _run_skill_summary(
        text=evidence.text,
        model=args.skill_model,
        timeout_sec=max(30, int(cfg.summarizer.timeout_seconds)),
    )
    skill_summary = _summary_from_skill_text(skill_raw, evidence, _build_fallback_summary)

    native_preview = writer.preview(native_summary, evidence)
    skill_preview = writer.preview(skill_summary, evidence)

    safe_title = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._ -]", " ", native_summary.title).strip()
    safe_title = re.sub(r"\s+", " ", safe_title) or "untitled"
    out_root = Path(args.out_root)
    native_rel = (out_root / f"{stamp} {safe_title} [native].md").as_posix()
    skill_rel = (out_root / f"{stamp} {safe_title} [skill].md").as_posix()

    native_path = _write_preview_note(
        writer.vault_path,
        native_rel,
        str(native_preview.get("content", "")).rstrip() + "\n\n<!-- compare_mode:native -->\n",
    )
    skill_path = _write_preview_note(
        writer.vault_path,
        skill_rel,
        str(skill_preview.get("content", "")).rstrip() + "\n\n<!-- compare_mode:skill -->\n",
    )

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_url": args.source_url.strip(),
        "source_kind": ingest.source_kind,
        "evidence_chars": len((evidence.text or "").strip()),
        "native": {
            "note_path": str(native_path),
            "title": native_summary.title,
            "conclusion": native_summary.conclusion,
            "bullets_count": len(native_summary.bullets),
            "error": native_error,
        },
        "skill": {
            "note_path": str(skill_path),
            "title": skill_summary.title,
            "conclusion": skill_summary.conclusion,
            "bullets_count": len(skill_summary.bullets),
            "error": skill_error,
            "model": args.skill_model,
        },
    }
    report_dir = state_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"compare_obsidian_write_{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"native_note={native_path}")
    print(f"skill_note={skill_path}")
    print(f"report_json={report_path}")
    if native_error:
        print(f"native_error={native_error}")
    if skill_error:
        print(f"skill_error={skill_error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
