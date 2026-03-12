#!/usr/bin/env python3
"""Run repeated multi-type validation and emit stability reports."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Dict, List, Tuple


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_cases(path: Path) -> List[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _init_bucket() -> dict:
    return {"pass": 0, "total": 0}


def _pct(pass_count: int, total: int) -> float:
    return round((pass_count / total) if total else 0.0, 4)


def _render_markdown(report: dict) -> str:
    lines: List[str] = []
    lines.append("# Stability Validation Report")
    lines.append("")
    lines.append(f"- generated_at: {report['generated_at']}")
    lines.append(f"- rounds: {report['rounds']}")
    lines.append(f"- cases: {report['case_count']}")
    lines.append(f"- cases_path: {report['cases_path']}")
    lines.append("")

    lines.append("## Model Overview")
    lines.append("")
    lines.append("| model | pass | total | pass_rate | total_cost_usd | avg_cost_per_round |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for model, item in report["models"].items():
        lines.append(
            f"| {model} | {item['pass_count']} | {item['total_count']} | {item['pass_rate']} | "
            f"{item['total_cost_usd']} | {item['avg_cost_per_round_usd']} |"
        )
    lines.append("")

    lines.append("## Source Type Stability")
    lines.append("")
    lines.append("| model | source_kind | pass | total | pass_rate |")
    lines.append("|---|---|---:|---:|---:|")
    for model, kinds in report["by_source_kind"].items():
        for kind, item in sorted(kinds.items(), key=lambda pair: pair[0]):
            lines.append(
                f"| {model} | {kind} | {item['pass']} | {item['total']} | {_pct(item['pass'], item['total'])} |"
            )
    lines.append("")

    lines.append("## Case Stability")
    lines.append("")
    model_names = list(report["models"].keys())
    lines.append("| case_id | source_kind | " + " | ".join([f"{name} pass/total(rate)" for name in model_names]) + " |")
    lines.append("|---|---|" + "|".join(["---"] * len(model_names)) + "|")
    for case_id, item in sorted(report["cases"].items(), key=lambda pair: pair[0]):
        row = [case_id, item["source_kind"]]
        for name in model_names:
            stat = item["models"].get(name, {"pass": 0, "total": 0})
            row.append(f"{stat['pass']}/{stat['total']} ({_pct(stat['pass'], stat['total'])})")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## Unstable Cases")
    lines.append("")
    unstable = report.get("unstable_cases", [])
    if not unstable:
        lines.append("- none")
    else:
        for item in unstable:
            lines.append(
                f"- case={item['case_id']} model={item['model']} pass={item['pass']}/{item['total']} "
                f"rate={item['pass_rate']} root_causes={item['root_causes']}"
            )
    lines.append("")

    lines.append("## Round Reports")
    lines.append("")
    lines.append("| round | model | pass_rate | cost_usd | report_json | report_markdown |")
    lines.append("|---:|---|---:|---:|---|---|")
    for row in report["round_reports"]:
        lines.append(
            f"| {row['round']} | {row['model']} | {row['pass_rate']} | {row['cost_usd']} | "
            f"{row['report_json']} | {row['report_markdown']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    root = _project_root()
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from openclaw_capture_workflow.accuracy_eval import run_accuracy_eval, save_accuracy_report

    parser = argparse.ArgumentParser(description="Run repeated stability validation")
    parser.add_argument("--config", default=str(root / "config.json"))
    parser.add_argument("--cases", default=str(root / "scripts" / "accuracy_eval_cases.multitype.json"))
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--models", default="gpt-4o-mini,gpt-4.1", help="comma-separated model list")
    parser.add_argument("--output-dir", default=str(root / "state" / "reports"))
    parser.add_argument("--summary-price-in", type=float, default=0.15)
    parser.add_argument("--summary-price-out", type=float, default=0.60)
    args = parser.parse_args()

    rounds = max(1, int(args.rounds))
    models = [item.strip() for item in args.models.split(",") if item.strip()]
    if not models:
        raise ValueError("no models configured")

    cases_path = Path(args.cases).resolve()
    cases = _load_cases(cases_path)
    case_kind_map = {
        str(item.get("id", "")).strip(): str(item.get("source_kind", "")).strip() or "unknown"
        for item in cases
    }

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    model_stats: Dict[str, dict] = {
        model: {"pass_count": 0, "total_count": 0, "total_cost_usd": 0.0} for model in models
    }
    by_source_kind: Dict[str, Dict[str, dict]] = {model: defaultdict(_init_bucket) for model in models}
    case_stats: Dict[str, dict] = {
        case_id: {
            "source_kind": kind,
            "models": {model: {"pass": 0, "total": 0, "root_causes": defaultdict(int)} for model in models},
        }
        for case_id, kind in case_kind_map.items()
    }
    round_reports: List[dict] = []

    for round_idx in range(1, rounds + 1):
        for model in models:
            report = run_accuracy_eval(
                config_path=args.config,
                cases_path=str(cases_path),
                summary_mode="model",
                summary_model=model,
                enable_judge=False,
                summary_price_input_usd_per_million=float(args.summary_price_in),
                summary_price_output_usd_per_million=float(args.summary_price_out),
            )
            saved = save_accuracy_report(report, output_dir, name_prefix=f"stability_{model.replace('.', '_')}_r{round_idx:02d}")
            print(
                f"round={round_idx} model={model} pass={report.get('pass_count',0)}/{report.get('case_count',0)} "
                f"rate={report.get('pass_rate',0)} cost={report.get('total_cost_usd',0)}"
            )

            pass_count = int(report.get("pass_count", 0))
            total_count = int(report.get("case_count", 0))
            total_cost = float(report.get("total_cost_usd", 0.0))
            model_stats[model]["pass_count"] += pass_count
            model_stats[model]["total_count"] += total_count
            model_stats[model]["total_cost_usd"] += total_cost

            round_reports.append(
                {
                    "round": round_idx,
                    "model": model,
                    "pass_rate": report.get("pass_rate", 0.0),
                    "cost_usd": round(total_cost, 6),
                    "report_json": saved["json"],
                    "report_markdown": saved["markdown"],
                }
            )

            for item in report.get("results", []):
                case_id = str(item.get("case_id", "")).strip()
                if not case_id:
                    continue
                passed = bool(item.get("passed"))
                source_kind = str(item.get("source_kind", "")).strip() or case_kind_map.get(case_id, "unknown")
                bucket = by_source_kind[model][source_kind]
                bucket["total"] += 1
                if passed:
                    bucket["pass"] += 1

                if case_id not in case_stats:
                    case_stats[case_id] = {
                        "source_kind": source_kind,
                        "models": {name: {"pass": 0, "total": 0, "root_causes": defaultdict(int)} for name in models},
                    }
                case_bucket = case_stats[case_id]["models"][model]
                case_bucket["total"] += 1
                if passed:
                    case_bucket["pass"] += 1
                root_cause = str(item.get("root_cause", "pass"))
                case_bucket["root_causes"][root_cause] += 1

    models_view: Dict[str, dict] = {}
    for model, item in model_stats.items():
        total = int(item["total_count"])
        passed = int(item["pass_count"])
        cost = round(float(item["total_cost_usd"]), 6)
        models_view[model] = {
            "pass_count": passed,
            "total_count": total,
            "pass_rate": _pct(passed, total),
            "total_cost_usd": cost,
            "avg_cost_per_round_usd": round(cost / rounds, 6),
        }

    source_view: Dict[str, Dict[str, dict]] = {}
    for model, kinds in by_source_kind.items():
        source_view[model] = {}
        for kind, item in kinds.items():
            source_view[model][kind] = {"pass": int(item["pass"]), "total": int(item["total"])}

    cases_view: Dict[str, dict] = {}
    unstable_cases: List[dict] = []
    for case_id, item in case_stats.items():
        models_data: Dict[str, dict] = {}
        for model, stat in item["models"].items():
            pass_count = int(stat["pass"])
            total_count = int(stat["total"])
            root_causes = {key: int(value) for key, value in stat["root_causes"].items()}
            models_data[model] = {
                "pass": pass_count,
                "total": total_count,
                "pass_rate": _pct(pass_count, total_count),
                "root_causes": root_causes,
            }
            if total_count > 0 and pass_count < total_count:
                unstable_cases.append(
                    {
                        "case_id": case_id,
                        "model": model,
                        "pass": pass_count,
                        "total": total_count,
                        "pass_rate": _pct(pass_count, total_count),
                        "root_causes": root_causes,
                    }
                )
        cases_view[case_id] = {"source_kind": item["source_kind"], "models": models_data}

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": str(Path(args.config).resolve()),
        "cases_path": str(cases_path),
        "rounds": rounds,
        "case_count": len(case_kind_map),
        "models": models_view,
        "by_source_kind": source_view,
        "cases": cases_view,
        "unstable_cases": sorted(
            unstable_cases,
            key=lambda entry: (entry["model"], entry["pass_rate"], entry["case_id"]),
        ),
        "round_reports": round_reports,
    }

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"stability_combined_{stamp}.json"
    md_path = output_dir / f"stability_combined_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    print(f"combined_json={json_path}")
    print(f"combined_markdown={md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
