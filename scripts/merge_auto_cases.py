#!/usr/bin/env python3
"""Merge and de-duplicate auto case inbox into a JSON array."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    root = _project_root()
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from openclaw_capture_workflow.iterative_cases import load_auto_case_inbox, merge_recognition_cases

    parser = argparse.ArgumentParser(description="Merge auto-case inbox into a JSON file")
    parser.add_argument("--inbox", default=str(root / "state" / "cases" / "inbox.jsonl"))
    parser.add_argument("--out", default=str(root / "scripts" / "iterative_auto_cases.json"))
    args = parser.parse_args()

    cases = merge_recognition_cases(load_auto_case_inbox(args.inbox))
    out_path = Path(args.out).resolve()
    out_path.write_text(
        json.dumps([item.to_dict() for item in cases], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"cases={len(cases)}")
    print(f"out={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

