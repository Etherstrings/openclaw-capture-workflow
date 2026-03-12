"""CLI entrypoint for the local workflow service."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import AppConfig
from .processor import WorkflowProcessor
from .server import build_server
from .stock_pipeline import StockPipelineTrigger
from .storage import JobStore
from .summarizer import OpenAICompatibleSummarizer


def serve(args: argparse.Namespace) -> int:
    config = AppConfig.load(args.config)
    base_dir = Path(args.config).resolve().parent
    state_dir = config.ensure_state_dirs(base_dir)
    job_store = JobStore(state_dir / "jobs")
    summarizer = OpenAICompatibleSummarizer(config.summarizer)
    processor = WorkflowProcessor(config, job_store, summarizer, state_dir)
    processor.start()
    server = build_server(config.listen_host, config.listen_port, processor, job_store)
    try:
        print(f"openclaw-capture-workflow listening on http://{config.listen_host}:{config.listen_port}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        processor.stop()
    return 0


def stock_trigger(args: argparse.Namespace) -> int:
    pipeline = StockPipelineTrigger(repo=args.repo, workflow=args.workflow)
    result = pipeline.trigger(mode=args.mode)
    print(result.message)
    return 0


def stock_inspect(args: argparse.Namespace) -> int:
    pipeline = StockPipelineTrigger(repo=args.repo, workflow=args.workflow)
    result = pipeline.inspect()
    print(result.message)
    return 0


def stock_ensure_running(args: argparse.Namespace) -> int:
    pipeline = StockPipelineTrigger(repo=args.repo, workflow=args.workflow)
    result = pipeline.ensure_running(mode=args.mode)
    print(result.message)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenClaw local capture workflow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the local HTTP service")
    serve_parser.add_argument("--config", required=True, help="Path to config JSON")
    serve_parser.set_defaults(func=serve)

    trigger_parser = subparsers.add_parser("stock-trigger", help="Trigger the remote GitHub stock workflow")
    trigger_parser.add_argument("--repo", default="Etherstrings/daily_stock_analysis", help="GitHub repo slug")
    trigger_parser.add_argument("--workflow", default="daily_analysis.yml", help="Workflow file name")
    trigger_parser.add_argument("--mode", default="full", help="Workflow mode")
    trigger_parser.set_defaults(func=stock_trigger)

    inspect_parser = subparsers.add_parser("stock-inspect", help="Inspect the remote GitHub stock workflow")
    inspect_parser.add_argument("--repo", default="Etherstrings/daily_stock_analysis", help="GitHub repo slug")
    inspect_parser.add_argument("--workflow", default="daily_analysis.yml", help="Workflow file name")
    inspect_parser.set_defaults(func=stock_inspect)

    ensure_parser = subparsers.add_parser(
        "stock-ensure-running",
        help="Inspect the remote GitHub stock workflow and trigger it when needed",
    )
    ensure_parser.add_argument("--repo", default="Etherstrings/daily_stock_analysis", help="GitHub repo slug")
    ensure_parser.add_argument("--workflow", default="daily_analysis.yml", help="Workflow file name")
    ensure_parser.add_argument("--mode", default="full", help="Workflow mode")
    ensure_parser.set_defaults(func=stock_ensure_running)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
