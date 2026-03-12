#!/usr/bin/env python3
"""Run accuracy evaluation and emit JSON + Markdown reports."""

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

    from openclaw_capture_workflow.accuracy_eval import run_accuracy_eval, save_accuracy_report

    parser = argparse.ArgumentParser(description="Accuracy evaluator for OpenClaw capture workflow")
    parser.add_argument("--config", default=str(root / "config.json"), help="Path to config.json")
    parser.add_argument(
        "--cases",
        default=str(root / "scripts" / "accuracy_eval_cases.example.json"),
        help="Path to eval cases JSON",
    )
    parser.add_argument("--summary-mode", choices=["fallback", "model"], default="fallback")
    parser.add_argument("--summary-model", default="", help="Optional summary model override")
    parser.add_argument("--enable-judge", action="store_true", help="Enable judge model scoring")
    parser.add_argument("--judge-model", default="", help="Judge model name (optional)")
    parser.add_argument("--judge-api-base-url", default="", help="Judge API base URL (optional)")
    parser.add_argument("--judge-api-key", default="", help="Judge API key (optional)")
    parser.add_argument("--summary-price-in", type=float, default=0.15, help="Summary input USD / 1M tokens")
    parser.add_argument("--summary-price-out", type=float, default=0.60, help="Summary output USD / 1M tokens")
    parser.add_argument("--judge-price-in", type=float, default=0.15, help="Judge input USD / 1M tokens")
    parser.add_argument("--judge-price-out", type=float, default=0.60, help="Judge output USD / 1M tokens")
    parser.add_argument("--max-cases", type=int, default=0, help="Run only first N cases (0 means all)")
    parser.add_argument("--output-dir", default="", help="Optional output dir for reports")
    args = parser.parse_args()

    report = run_accuracy_eval(
        config_path=args.config,
        cases_path=args.cases,
        summary_mode=args.summary_mode,
        summary_model=args.summary_model,
        enable_judge=args.enable_judge,
        judge_model=args.judge_model,
        judge_api_base_url=args.judge_api_base_url,
        judge_api_key=args.judge_api_key,
        summary_price_input_usd_per_million=float(args.summary_price_in),
        summary_price_output_usd_per_million=float(args.summary_price_out),
        judge_price_input_usd_per_million=float(args.judge_price_in),
        judge_price_output_usd_per_million=float(args.judge_price_out),
        max_cases=int(args.max_cases),
    )

    if args.output_dir:
        out_dir = Path(args.output_dir).resolve()
    else:
        out_dir = (Path(args.config).resolve().parent / "state" / "reports").resolve()
    saved = save_accuracy_report(report, out_dir)

    print(f"cases={report.get('case_count', 0)} pass={report.get('pass_count', 0)} rate={report.get('pass_rate', 0)}")
    print(f"total_cost_usd={report.get('total_cost_usd', 0)}")
    print(f"report_json={saved['json']}")
    print(f"report_markdown={saved['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
