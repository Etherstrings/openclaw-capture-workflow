#!/usr/bin/env python3
"""Clear generated OpenClaw vault content and rebuild a random 15-note showcase."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import random
import re
import shutil
import sys
from typing import Any, Dict, Iterable, List
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_src() -> None:
    root = _project_root()
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


_TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "share_id",
    "share_source",
    "share_medium",
    "share_session_id",
    "share_from",
    "share_tag",
    "apptime",
    "shareRedId",
    "author_share",
    "xsec_source",
    "xsec_token",
    "spm_id_from",
    "from_spmid",
    "timestamp",
    "unique_k",
    "mid",
    "buvid",
    "vd_source",
    "wechatWid",
    "wechatOrigin",
    "note_flow_source",
}

_ALLOWED_DOMAINS = (
    "bilibili.com",
    "b23.tv",
    "xiaohongshu.com",
    "youtube.com",
    "youtu.be",
    "github.com",
    "docs.openclaw.ai",
    "docs.python.org",
    "developers.openai.com",
    "pinchtab.com",
    "cloud.tencent.com",
)


@dataclass
class Candidate:
    source_url: str
    source_kind: str
    family: str
    bucket: str
    title: str
    classification: str
    text_chars: int
    evidence: Any


def _normalize_raw_url(url: str) -> str:
    text = str(url or "").strip().strip("`").strip()
    if text and not text.startswith(("http://", "https://")) and re.search(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
        text = "https://" + text.lstrip("/")
    return text


def _canonicalize_general_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    items = parse_qsl(parsed.query, keep_blank_values=False)
    filtered = [(k, v) for k, v in items if k not in _TRACKING_QUERY_KEYS and not k.startswith("utm_")]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", urlencode(filtered, doseq=True), ""))


def _infer_source_kind(url: str, explicit: str | None = None) -> str:
    if explicit in {"url", "video_url"}:
        return explicit
    lowered = url.lower()
    if any(token in lowered for token in ["bilibili.com/video/", "b23.tv/", "youtube.com/watch", "youtu.be/", "xiaohongshu.com/explore/"]):
        return "video_url"
    return "url"


def _family_for_url(url: str) -> str:
    lowered = url.lower()
    if "bilibili.com" in lowered or "b23.tv" in lowered:
        return "bilibili"
    if "xiaohongshu.com" in lowered:
        return "xiaohongshu"
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return "youtube"
    if "github.com" in lowered:
        return "github"
    if any(token in lowered for token in ["docs.openclaw.ai", "docs.python.org", "developers.openai.com", "pinchtab.com"]):
        return "docs"
    return "web"


def _bucket_for(url: str, source_kind: str) -> str:
    family = _family_for_url(url)
    lowered = url.lower()
    if family == "github":
        return "github_doc" if "/blob/" in lowered else "github_repo"
    if family == "docs":
        return "docs_web"
    if family == "web" and "cloud.tencent.com" in lowered:
        return "tutorial_web"
    if source_kind == "video_url":
        return f"{family}_video"
    return f"{family}_page"


def _looks_like_candidate_url(url: str) -> bool:
    if not url or any(ch in url for ch in ["\n", "\r", " "]):
        return False
    lowered = url.lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    if any(token in lowered for token in [".js", ".png", ".jpg", ".jpeg", ".webp", "service-worker", "socket-worker"]):
        return False
    return any(domain in lowered for domain in _ALLOWED_DOMAINS)


def _iter_json_source_urls(node: Any) -> Iterable[tuple[str, str | None]]:
    if isinstance(node, dict):
        url = node.get("source_url")
        source_kind = node.get("source_kind")
        if isinstance(url, str) and url.strip():
            yield url, str(source_kind).strip() if isinstance(source_kind, str) else None
        for value in node.values():
            yield from _iter_json_source_urls(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_json_source_urls(item)


def _collect_candidate_urls(extra_urls: list[str], extra_video_urls: list[str]) -> list[tuple[str, str | None]]:
    root = _project_root()
    candidates: list[tuple[str, str | None]] = []
    for path in [
        root / "scripts" / "accuracy_eval_cases.example.json",
        root / "scripts" / "accuracy_eval_cases.multitype.json",
        root / "scripts" / "accuracy_eval_cases.new_videos.json",
        root / "scripts" / "accuracy_eval_cases.enumerated_videos.json",
        root / "scripts" / "robot_ingest_regression_cases.json",
        root / "scripts" / "iterative_recognition_cases.json",
    ]:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        candidates.extend(list(_iter_json_source_urls(data)))

    regex = re.compile(r"https?://[^\s)>\]\"']+")
    for path in [
        root / "docs" / "SESSION_RESTORE_20260313.md",
        root / "docs" / "NEXT_SESSION_HANDOFF.md",
    ]:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in regex.findall(text):
            candidates.append((match.rstrip(".,`"), None))

    for item in extra_urls:
        candidates.append((item, None))
    for item in extra_video_urls:
        candidates.append((item, "video_url"))
    return candidates


def _probe_classification(evidence: Any) -> str:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    text_chars = len((evidence.text or "").strip())
    signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
    title = str(evidence.title or "").strip()
    if any(token in title for token in ["页面未找到", "页面不见了", "无法浏览", "404"]):
        return "blocked"
    if evidence.source_kind == "video_url":
        tracks = metadata.get("tracks", {}) if isinstance(metadata.get("tracks"), dict) else {}
        has_track = any(bool(tracks.get(key)) for key in ["has_subtitle", "has_transcript", "has_keyframes", "has_keyframe_ocr"])
        sources = metadata.get("evidence_sources", []) if isinstance(metadata.get("evidence_sources"), list) else []
        if has_track and text_chars >= 120:
            return "successful"
        if has_track or text_chars >= 100 or (sources and len(sources) > 1):
            return "partial_but_usable"
        return "blocked"
    structured = metadata.get("structured_document", {}) if isinstance(metadata.get("structured_document"), dict) else {}
    if structured or text_chars >= 160 or signals:
        return "successful"
    if text_chars >= 90:
        return "partial_but_usable"
    return "blocked"


def _selection_order(candidates: list[Candidate], *, seed: int, sample_size: int, max_per_family: int) -> list[Candidate]:
    usable = [cand for cand in candidates if cand.classification in {"successful", "partial_but_usable"}]
    def rank(c: Candidate) -> tuple[int, str]:
        return (0 if c.classification == "successful" else 1, c.source_url)

    buckets: dict[str, list[Candidate]] = defaultdict(list)
    for cand in usable:
        buckets[cand.bucket].append(cand)

    rng = random.Random(seed)
    ordered: list[Candidate] = []
    family_counts: dict[str, int] = defaultdict(int)
    seen: set[str] = set()

    for bucket in sorted(buckets):
        bucket_items = sorted(buckets[bucket], key=rank)
        rng.shuffle(bucket_items)
        bucket_items.sort(key=rank)
        for cand in bucket_items:
            if cand.source_url in seen or family_counts[cand.family] >= max_per_family:
                continue
            ordered.append(cand)
            seen.add(cand.source_url)
            family_counts[cand.family] += 1
            break

    remaining = [cand for cand in usable if cand.source_url not in seen]
    rng.shuffle(remaining)
    remaining.sort(key=rank)
    for cand in remaining:
        if len(ordered) >= sample_size:
            break
        if family_counts[cand.family] >= max_per_family:
            continue
        ordered.append(cand)
        seen.add(cand.source_url)
        family_counts[cand.family] += 1
    return ordered


def _write_cleanup_manifest(report_dir: Path, vault: Path, targets: list[Path]) -> Path:
    removed_files: list[str] = []
    for path in targets:
        if not path.exists():
            continue
        if path.is_file():
            removed_files.append(str(path.relative_to(vault)))
        else:
            for item in sorted(path.rglob("*")):
                if item.is_file():
                    removed_files.append(str(item.relative_to(vault)))
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "vault": str(vault),
        "targets": [str(path.relative_to(vault)) for path in targets],
        "removed_file_count": len(removed_files),
        "removed_files": removed_files,
    }
    path = report_dir / f"obsidian_cleanup_manifest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _clear_generated_vault_content(vault: Path) -> None:
    for rel in [Path("Inbox/OpenClaw"), Path("Topics"), Path("Entities")]:
        path = vault / rel
        if path.exists():
            shutil.rmtree(path)


def _build_index_note(vault: Path, rows: list[dict[str, object]]) -> Path:
    rel = Path("Inbox/OpenClaw/Showcase/00_random15_index.md")
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Random 15 Index",
        "",
        f"- generated_at: {datetime.now().isoformat(timespec='seconds')}",
        f"- total_notes: {len(rows)}",
        "",
        "| # | Title | Site | URL | L1 | L2 | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for idx, row in enumerate(rows, start=1):
        lines.append(
            f"| {idx} | {row['title']} | {row['family']} | {row['url']} | {row['keyword_l1']} | "
            f"{', '.join(row['keyword_l2']) if isinstance(row['keyword_l2'], list) else row['keyword_l2']} | [[{row['note_path']}]] |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_rebuild_report(report_dir: Path, rows: list[dict[str, object]], cleanup_manifest: Path) -> Path:
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cleanup_manifest": str(cleanup_manifest),
        "notes_written": rows,
    }
    path = report_dir / f"obsidian_random15_rebuild_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    _load_src()
    from openclaw_capture_workflow.config import AppConfig
    from openclaw_capture_workflow.extractor import EvidenceExtractor, _canonicalize_video_source_url
    from openclaw_capture_workflow.models import IngestRequest
    from openclaw_capture_workflow.note_renderer import OpenAICompatibleNoteRenderer
    from openclaw_capture_workflow.obsidian import ObsidianWriter
    from openclaw_capture_workflow.summarizer import OpenAICompatibleSummarizer

    parser = argparse.ArgumentParser(description="Rebuild Obsidian with 15 random analyzed examples")
    parser.add_argument("--config", default=str(_project_root() / "config.json"))
    parser.add_argument("--seed", type=int, default=20260314)
    parser.add_argument("--sample-size", type=int, default=15)
    parser.add_argument("--max-per-family", type=int, default=5)
    parser.add_argument("--extra-url", action="append", default=[])
    parser.add_argument("--extra-video-url", action="append", default=[])
    args = parser.parse_args()

    config = AppConfig.load(args.config)
    base_dir = Path(args.config).resolve().parent
    state_dir = config.ensure_state_dirs(base_dir)
    report_dir = state_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    vault = Path(config.obsidian.vault_path).expanduser()
    vault.mkdir(parents=True, exist_ok=True)

    raw_candidates = _collect_candidate_urls(args.extra_url, args.extra_video_url)
    deduped: dict[str, tuple[str, str]] = {}
    for raw_url, explicit_kind in raw_candidates:
        normalized = _normalize_raw_url(raw_url)
        if not _looks_like_candidate_url(normalized):
            continue
        source_kind = _infer_source_kind(normalized, explicit_kind)
        if source_kind == "video_url":
            canonical = _canonicalize_video_source_url(normalized) or normalized
        else:
            canonical = _canonicalize_general_url(normalized)
        if not _looks_like_candidate_url(canonical):
            continue
        deduped.setdefault(canonical, (canonical, source_kind))

    extractor = EvidenceExtractor(config, state_dir / "artifacts")
    candidates: list[Candidate] = []
    for canonical, source_kind in deduped.values():
        try:
            evidence = extractor.extract(
                IngestRequest(
                    chat_id="-1",
                    reply_to_message_id="1",
                    request_id=f"rebuild-probe-{abs(hash(canonical))}",
                    source_kind=source_kind,
                    source_url=canonical,
                    raw_text=canonical,
                    requested_output_lang="zh-CN",
                    dry_run=True,
                    video_probe_seconds=180 if source_kind == "video_url" else None,
                )
            )
        except Exception:
            continue
        candidates.append(
            Candidate(
                source_url=evidence.source_url or canonical,
                source_kind=source_kind,
                family=_family_for_url(evidence.source_url or canonical),
                bucket=_bucket_for(evidence.source_url or canonical, source_kind),
                title=str(evidence.title or "").strip() or canonical,
                classification=_probe_classification(evidence),
                text_chars=len((evidence.text or "").strip()),
                evidence=evidence,
            )
        )

    ordered = _selection_order(candidates, seed=args.seed, sample_size=max(len(candidates), args.sample_size), max_per_family=args.max_per_family)
    if len(ordered) < args.sample_size:
        raise RuntimeError(f"not enough usable candidates: found {len(ordered)}")

    cleanup_targets = [vault / "Inbox" / "OpenClaw", vault / "Topics", vault / "Entities"]
    cleanup_manifest = _write_cleanup_manifest(report_dir, vault, cleanup_targets)
    _clear_generated_vault_content(vault)

    writer = ObsidianWriter(config.obsidian, renderer=OpenAICompatibleNoteRenderer(config.summarizer), materials_root=state_dir / "materials")
    summarizer = OpenAICompatibleSummarizer(config.summarizer)
    rows: list[dict[str, object]] = []
    for candidate in ordered:
        if len(rows) >= args.sample_size:
            break
        try:
            summary = summarizer.summarize(candidate.evidence)
            note_meta = writer.write(summary, candidate.evidence, use_model_render=True)
        except Exception:
            continue
        rows.append(
            {
                "title": summary.title,
                "family": candidate.family,
                "url": candidate.source_url,
                "source_kind": candidate.source_kind,
                "classification": candidate.classification,
                "keyword_l1": note_meta.get("keyword_l1", ""),
                "keyword_l2": [item for item in str(note_meta.get("keyword_l2", "")).split(",") if item],
                "note_path": note_meta["note_path"],
                "keyword_links": note_meta.get("keyword_links", []),
            }
        )

    if len(rows) < args.sample_size:
        raise RuntimeError(f"only wrote {len(rows)} notes, expected {args.sample_size}")

    index_path = _build_index_note(vault, rows)
    report_path = _write_rebuild_report(report_dir, rows, cleanup_manifest)
    print(f"cleanup_manifest={cleanup_manifest}")
    print(f"rebuild_report={report_path}")
    print(f"index_note={index_path}")
    print(f"notes_written={len(rows)}")
    for row in rows:
        print(
            f"title={row['title']} family={row['family']} keyword_l1={row['keyword_l1']} "
            f"keyword_l2={','.join(row['keyword_l2']) if isinstance(row['keyword_l2'], list) else row['keyword_l2']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
