#!/usr/bin/env python3
"""Clean OpenClaw-managed notes/topic links inside an Obsidian vault."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple


TOPIC_LINK_RE = re.compile(r"\[\[Topics/([^/\]]+)/[^\]]+\]\]")
SECTION_HEADING_RE = re.compile(r"^##\s+")
WIKI_LINK_LINE_RE = re.compile(r"^\s*-\s+\[\[([^\]]+)\]\]\s*$")


@dataclass
class OpenClawNote:
    path: Path
    rel: str
    title: str
    source_url: str
    raw_evidence: str
    topic_names: List[str]


def parse_frontmatter(text: str) -> Dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    payload = text[4:end]
    result: Dict[str, str] = {}
    for line in payload.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def extract_section(text: str, section_title: str) -> str:
    marker = f"## {section_title}"
    idx = text.find(marker)
    if idx < 0:
        return ""
    body = text[idx + len(marker) :].lstrip("\n")
    lines: List[str] = []
    for line in body.splitlines():
        if SECTION_HEADING_RE.match(line):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def load_openclaw_notes(vault: Path, inbox_root: str) -> List[OpenClawNote]:
    base = vault / inbox_root
    notes: List[OpenClawNote] = []
    if not base.exists():
        return notes
    for path in sorted(base.rglob("*.md")):
        rel = path.relative_to(vault).as_posix()
        if "/_Archive/" in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        frontmatter = parse_frontmatter(text)
        title = frontmatter.get("title", "").strip() or path.stem
        source_url = frontmatter.get("source_url", "").strip()
        raw_evidence = extract_section(text, "原始证据")
        topic_names = sorted(set(TOPIC_LINK_RE.findall(text)))
        notes.append(
            OpenClawNote(
                path=path,
                rel=rel,
                title=title,
                source_url=source_url,
                raw_evidence=raw_evidence,
                topic_names=topic_names,
            )
        )
    return notes


def choose_duplicates(notes: List[OpenClawNote]) -> List[Tuple[OpenClawNote, OpenClawNote]]:
    by_source: Dict[str, List[OpenClawNote]] = {}
    by_content: Dict[Tuple[str, str], List[OpenClawNote]] = {}
    for note in notes:
        if note.source_url:
            by_source.setdefault(note.source_url, []).append(note)
        else:
            key = (note.title, note.raw_evidence)
            if key[1]:
                by_content.setdefault(key, []).append(note)
    duplicates: List[Tuple[OpenClawNote, OpenClawNote]] = []
    for group in list(by_source.values()) + list(by_content.values()):
        if len(group) <= 1:
            continue
        group_sorted = sorted(group, key=lambda item: item.rel)
        keep = group_sorted[-1]
        for old in group_sorted[:-1]:
            duplicates.append((old, keep))
    return duplicates


def read_topic_links_from_index(path: Path) -> List[str]:
    links: List[str] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = WIKI_LINK_LINE_RE.match(line)
        if not m:
            continue
        target = m.group(1)
        if "|" in target:
            target = target.split("|", 1)[0]
        if "#" in target:
            target = target.split("#", 1)[0]
        links.append(target)
    return links


def write_topic_index(path: Path, topic: str, note_links: List[str], apply: bool) -> None:
    content = "# " + topic + "\n\n## 笔记\n"
    for link in note_links:
        content += f"- [[{link}]]\n"
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def cleanup(vault: Path, inbox_root: str, topics_root: str, apply: bool) -> None:
    notes = load_openclaw_notes(vault, inbox_root)
    print(f"Active OpenClaw notes: {len(notes)}")

    duplicates = choose_duplicates(notes)
    print(f"Detected duplicates: {len(duplicates)}")
    for old, keep in duplicates:
        print(f"- duplicate: {old.rel} -> keep {keep.rel}")
        if apply:
            archive_path = vault / inbox_root / "_Archive" / "_Dedup" / old.rel.replace("/", "__")
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old.path), str(archive_path))

    # Re-scan after dedup moves.
    notes = load_openclaw_notes(vault, inbox_root)
    desired_topic_links: Dict[str, Set[str]] = {}
    for note in notes:
        for topic in note.topic_names:
            desired_topic_links.setdefault(topic, set()).add(note.rel)

    topics_base = vault / topics_root
    existing_indexes = sorted(topics_base.rglob("* Index.md")) if topics_base.exists() else []
    touched_indexes = 0
    removed_indexes = 0
    created_indexes = 0

    for idx in existing_indexes:
        topic_name = idx.parent.name
        current_links = read_topic_links_from_index(idx)
        has_non_openclaw = any(not link.startswith(f"{inbox_root}/") for link in current_links)
        if has_non_openclaw:
            continue
        desired_links = sorted(desired_topic_links.get(topic_name, set()))
        if desired_links:
            touched_indexes += 1
            write_topic_index(idx, topic_name, desired_links, apply=apply)
        else:
            removed_indexes += 1
            print(f"- remove empty topic index: {idx.relative_to(vault).as_posix()}")
            if apply:
                idx.unlink(missing_ok=True)
                try:
                    next(idx.parent.iterdir())
                except StopIteration:
                    idx.parent.rmdir()

    existing_topic_names = {p.parent.name for p in existing_indexes}
    for topic_name, links in sorted(desired_topic_links.items()):
        if topic_name in existing_topic_names:
            continue
        created_indexes += 1
        idx = topics_base / topic_name / f"{topic_name} Index.md"
        print(f"- create topic index: {idx.relative_to(vault).as_posix()}")
        write_topic_index(idx, topic_name, sorted(links), apply=apply)

    print(f"Topic indexes updated: {touched_indexes}")
    print(f"Topic indexes removed: {removed_indexes}")
    print(f"Topic indexes created: {created_indexes}")
    print("APPLY MODE:", apply)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    vault = Path(cfg["obsidian"]["vault_path"]).expanduser()
    inbox_root = cfg["obsidian"]["inbox_root"]
    topics_root = cfg["obsidian"]["topics_root"]
    cleanup(vault=vault, inbox_root=inbox_root, topics_root=topics_root, apply=args.apply)


if __name__ == "__main__":
    main()
