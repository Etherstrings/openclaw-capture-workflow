#!/usr/bin/env python3
"""Run iterative recognition with baseline/search/chosen previews."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    root = _project_root()
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from openclaw_capture_workflow.iterative_runner import run_iterative_recognition

    parser = argparse.ArgumentParser(description="Run iterative recognition over manual/auto cases")
    parser.add_argument("--config", default=str(root / "config.json"))
    parser.add_argument("--cases", default=str(root / "scripts" / "iterative_recognition_cases.json"))
    parser.add_argument("--case-source", choices=["manual", "auto", "mixed"], default="mixed")
    parser.add_argument("--auto-inbox", default="", help="Auto-case inbox jsonl path")
    parser.add_argument("--search-template", default="https://duckduckgo.com/html/?q={query}")
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--max-pages", type=int, default=2)
    args = parser.parse_args()

    report = run_iterative_recognition(
        config_path=args.config,
        cases_path=args.cases,
        case_source=args.case_source,
        auto_inbox_path=args.auto_inbox,
        search_template=args.search_template,
        max_results=max(1, int(args.max_results)),
        max_pages=max(1, int(args.max_pages)),
    )
    print(f"cases={report.get('case_count', 0)}")
    print(f"report_markdown={report.get('report_markdown', '')}")
    print(f"report_json={report.get('report_json', '')}")
    for item in report.get("results", []):
        print(
            f"case={item.get('case_id')} "
            f"baseline={item.get('baseline', {}).get('overall_score')} "
            f"searched={item.get('searched', {}).get('overall_score')} "
            f"chosen={item.get('chosen', {}).get('label')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

