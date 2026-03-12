#!/usr/bin/env python3
"""Run multi-type progressive validation across model tiers."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _build_summary_table(stage_reports: list[tuple[str, dict]]) -> str:
    all_case_ids: list[str] = []
    for _, report in stage_reports:
        for item in report.get("results", []):
            cid = str(item.get("case_id", ""))
            if cid and cid not in all_case_ids:
                all_case_ids.append(cid)

    lines: list[str] = []
    lines.append("# Progressive Validation Report")
    lines.append("")
    lines.append(f"- generated_at: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("## Stage Summary")
    lines.append("")
    lines.append("| stage | case_count | pass_count | pass_rate | total_cost_usd |")
    lines.append("|---|---:|---:|---:|---:|")
    for stage_name, report in stage_reports:
        lines.append(
            f"| {stage_name} | {report.get('case_count',0)} | {report.get('pass_count',0)} | "
            f"{report.get('pass_rate',0)} | {report.get('total_cost_usd',0)} |"
        )
    lines.append("")
    lines.append("## Case Matrix")
    lines.append("")
    lines.append("| case_id | " + " | ".join([f"{name} (pass/score/root)" for name, _ in stage_reports]) + " |")
    lines.append("|---|" + "|".join(["---"] * len(stage_reports)) + "|")
    for case_id in all_case_ids:
        cells: list[str] = []
        for _, report in stage_reports:
            item = next((r for r in report.get("results", []) if r.get("case_id") == case_id), None)
            if not item:
                cells.append("N/A")
                continue
            cells.append(
                f"{str(item.get('passed', False)).lower()} / {item.get('overall_score',0)} / {item.get('root_cause','')}"
            )
        lines.append(f"| {case_id} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Failing Cases")
    lines.append("")
    for stage_name, report in stage_reports:
        failed = [item for item in report.get("results", []) if not item.get("passed")]
        if not failed:
            continue
        lines.append(f"### {stage_name}")
        lines.append("")
        for item in failed:
            lines.append(f"- {item.get('case_id')}: {item.get('root_cause')} | missing={item.get('missing', [])}")
            lines.append(f"  preview={item.get('preview', {}).get('file', '')}")
        lines.append("")
    if all(not [item for item in report.get("results", []) if not item.get("passed")] for _, report in stage_reports):
        lines.append("- All stages passed all cases.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    root = _project_root()
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from openclaw_capture_workflow.accuracy_eval import run_accuracy_eval, save_accuracy_report

    parser = argparse.ArgumentParser(description="Progressive multi-type validation")
    parser.add_argument("--config", default=str(root / "config.json"))
    parser.add_argument("--cases", default=str(root / "scripts" / "accuracy_eval_cases.multitype.json"))
    parser.add_argument("--mini-model", default="gpt-4o-mini")
    parser.add_argument("--strong-model", default="gpt-4.1")
    parser.add_argument("--output-dir", default=str(root / "state" / "reports"))
    parser.add_argument("--max-cases", type=int, default=0)
    args = parser.parse_args()

    stages = [
        ("fallback", {"summary_mode": "fallback", "summary_model": ""}),
        ("mini", {"summary_mode": "model", "summary_model": args.mini_model}),
        ("strong", {"summary_mode": "model", "summary_model": args.strong_model}),
    ]

    output_dir = Path(args.output_dir).resolve()
    stage_reports: list[tuple[str, dict]] = []
    for stage_name, params in stages:
        report = run_accuracy_eval(
            config_path=args.config,
            cases_path=args.cases,
            summary_mode=params["summary_mode"],
            summary_model=params["summary_model"],
            enable_judge=False,
            max_cases=int(args.max_cases),
        )
        saved = save_accuracy_report(report, output_dir, name_prefix=f"progressive_{stage_name}")
        report["saved"] = saved
        stage_reports.append((stage_name, report))
        print(
            f"{stage_name}: pass={report.get('pass_count',0)}/{report.get('case_count',0)} "
            f"rate={report.get('pass_rate',0)} cost={report.get('total_cost_usd',0)} json={saved['json']}"
        )

    merged = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": str(Path(args.config).resolve()),
        "cases": str(Path(args.cases).resolve()),
        "stages": [
            {
                "stage": stage_name,
                "pass_count": report.get("pass_count", 0),
                "case_count": report.get("case_count", 0),
                "pass_rate": report.get("pass_rate", 0),
                "total_cost_usd": report.get("total_cost_usd", 0),
                "report_json": report.get("saved", {}).get("json"),
                "report_markdown": report.get("saved", {}).get("markdown"),
            }
            for stage_name, report in stage_reports
        ],
    }
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    merged_json = output_dir / f"progressive_combined_{stamp}.json"
    merged_md = output_dir / f"progressive_combined_{stamp}.md"
    merged_json.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    merged_md.write_text(_build_summary_table(stage_reports), encoding="utf-8")
    print(f"combined_json={merged_json}")
    print(f"combined_markdown={merged_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

