#!/usr/bin/env python3
"""Replay saved robot payloads against the local /ingest endpoint."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from urllib import request as urlrequest


def _load_cases(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("cases file must be a JSON list")
    return [item for item in payload if isinstance(item, dict)]


def _post_json(base_url: str, payload: dict) -> dict:
    req = urlrequest.Request(
        url=base_url.rstrip("/") + "/ingest",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay saved robot payloads to the local ingest service")
    parser.add_argument(
        "--cases",
        default="scripts/robot_ingest_regression_cases.json",
        help="Path to saved robot payload cases",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8765", help="Local workflow base URL")
    parser.add_argument("--limit", type=int, default=0, help="Replay at most N cases (0 means all)")
    args = parser.parse_args()

    cases_path = Path(args.cases).resolve()
    cases = _load_cases(cases_path)
    selected = cases[: args.limit] if args.limit and args.limit > 0 else cases
    results: list[dict] = []
    for item in selected:
        payload = item.get("payload", {})
        if not isinstance(payload, dict):
            continue
        response = _post_json(args.base_url, payload)
        results.append(
            {
                "case_id": item.get("case_id", ""),
                "entry_context": item.get("entry_context", {}),
                "request_id": payload.get("request_id", ""),
                "response": response,
            }
        )

    report = {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "base_url": args.base_url,
        "case_count": len(results),
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
