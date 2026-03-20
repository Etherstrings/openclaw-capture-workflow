"""CLI entrypoint for the local workflow service."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .api import run_api
from .analyzer import analyze_url
from .config import AppConfig
from .processor import WorkflowProcessor
from .server import build_server
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


def analyze_url_command(args: argparse.Namespace) -> int:
    try:
        config = AppConfig.load(args.config)
        base_dir = Path(args.config).resolve().parent
        state_dir = config.ensure_state_dirs(base_dir)
        outcome = analyze_url(
            url=args.url,
            requested_output_lang=args.requested_output_lang,
            config=config,
            state_dir=state_dir,
        )
        payload = json.dumps(outcome.document.to_dict(), ensure_ascii=False, indent=2)
        print(payload)
        if args.output_file:
            Path(args.output_file).write_text(payload + "\n", encoding="utf-8")
        for warning in outcome.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def serve_api_command(args: argparse.Namespace) -> int:
    try:
        return run_api(args.config, host=args.host, port=args.port)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenClaw local capture workflow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the local HTTP service")
    serve_parser.add_argument("--config", required=True, help="Path to config JSON")
    serve_parser.set_defaults(func=serve)

    serve_api_parser = subparsers.add_parser("serve-api", help="Run a thin HTTP wrapper for the URL analyzer")
    serve_api_parser.add_argument("--config", required=True, help="Path to config JSON")
    serve_api_parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    serve_api_parser.add_argument("--port", type=int, default=8775, help="Bind port")
    serve_api_parser.set_defaults(func=serve_api_command)

    analyze_parser = subparsers.add_parser("analyze-url", help="Analyze a URL into a structured JSON document")
    analyze_parser.add_argument("--config", required=True, help="Path to config JSON")
    analyze_parser.add_argument("--url", required=True, help="Absolute http(s) URL")
    analyze_parser.add_argument("--requested-output-lang", default="zh-CN", help="Preferred output language")
    analyze_parser.add_argument("--output-file", help="Optional path to save the JSON output")
    analyze_parser.set_defaults(func=analyze_url_command)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
