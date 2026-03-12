#!/usr/bin/env python3
"""Compare native summarizer vs summarize skill on the same extracted evidence."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _contains(corpus: str, item: str) -> bool:
    c = _norm_text(corpus).lower()
    i = _norm_text(item).lower()
    if not i:
        return True
    return i in c


def _required_items(expect: Any) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for value in getattr(expect, "required_keywords", []):
        pairs.append(("keyword", str(value)))
    for value in getattr(expect, "required_links", []):
        pairs.append(("link", str(value)))
    for value in getattr(expect, "required_projects", []):
        pairs.append(("project", str(value)))
    for value in getattr(expect, "required_skill_ids", []):
        pairs.append(("skill_id", str(value)))
    for value in getattr(expect, "required_skills", []):
        pairs.append(("skill", str(value)))
    for value in getattr(expect, "required_actions", []):
        pairs.append(("action", str(value)))
    return pairs


def _evaluate_text(text: str, expect: Any) -> dict[str, Any]:
    required = _required_items(expect)
    missing: list[str] = []
    hit = 0
    for label, value in required:
        if _contains(text, value):
            hit += 1
        else:
            missing.append(f"{label}:{value}")
    recall = (hit / len(required)) if required else 1.0

    forbidden_hits: list[str] = []
    for item in getattr(expect, "forbidden_phrases", []):
        if _contains(text, str(item)):
            forbidden_hits.append(str(item))

    length_penalty = 0.0
    min_chars = int(getattr(expect, "min_evidence_chars", 80))
    text_chars = len(_norm_text(text))
    if text_chars < min_chars:
        length_penalty = min(0.35, (min_chars - text_chars) / max(min_chars, 1) * 0.35)

    score = max(0.0, recall - min(0.4, 0.2 * len(forbidden_hits)) - length_penalty)
    passed = (score >= 0.78) and (not missing) and (not forbidden_hits)
    return {
        "score": round(score, 4),
        "recall": round(recall, 4),
        "passed": bool(passed),
        "missing": missing,
        "forbidden_hits": forbidden_hits,
        "text_chars": text_chars,
    }


def _native_summary_text(summary: Any) -> str:
    parts: list[str] = []
    for key in ["title", "conclusion"]:
        value = getattr(summary, key, "")
        if value:
            parts.append(str(value))
    for key in ["bullets", "evidence_quotes", "follow_up_actions"]:
        values = getattr(summary, key, [])
        if isinstance(values, list):
            parts.extend([str(item) for item in values if str(item).strip()])
    return "\n".join(parts)


def _run_skill_summary(evidence_text: str, model: str, timeout_sec: int) -> tuple[str, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
        tmp.write(evidence_text or "")
        tmp_path = tmp.name
    cmd = [
        "summarize",
        tmp_path,
        "--length",
        "short",
        "--max-output-tokens",
        "700",
        "--model",
        model,
    ]
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=max(20, int(timeout_sec)),
        )
        return result.stdout.strip(), ""
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        return stdout, f"skill_cmd_failed: {stderr or stdout or str(exc)}"
    except subprocess.TimeoutExpired:
        return "", "skill_cmd_timeout"
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


def _render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Native vs Skill Summary Compare")
    lines.append("")
    lines.append(f"- generated_at: {report['generated_at']}")
    lines.append(f"- cases: {report['case_count']}")
    lines.append(f"- native_model: {report['native_model']}")
    lines.append(f"- skill_model: {report['skill_model']}")
    lines.append("")
    lines.append("## Overall")
    lines.append("")
    lines.append("| side | pass | total | pass_rate | avg_score |")
    lines.append("|---|---:|---:|---:|---:|")
    for side in ["native", "skill"]:
        item = report["overall"][side]
        lines.append(
            f"| {side} | {item['pass_count']} | {item['total_count']} | {item['pass_rate']} | {item['avg_score']} |"
        )
    lines.append("")
    lines.append("## Per Case")
    lines.append("")
    lines.append("| case_id | native(pass/score) | skill(pass/score) | winner |")
    lines.append("|---|---|---|---|")
    for row in report["results"]:
        n = row["native"]["eval"]
        s = row["skill"]["eval"]
        lines.append(
            f"| {row['case_id']} | {str(n['passed']).lower()} / {n['score']} | "
            f"{str(s['passed']).lower()} / {s['score']} | {row['winner']} |"
        )
    lines.append("")
    lines.append("## Fails")
    lines.append("")
    for row in report["results"]:
        n = row["native"]["eval"]
        s = row["skill"]["eval"]
        if n["passed"] and s["passed"]:
            continue
        lines.append(f"### {row['case_id']}")
        lines.append("")
        if not n["passed"]:
            lines.append(f"- native missing: {n['missing']}")
            lines.append(f"- native forbidden: {n['forbidden_hits']}")
            if row["native"]["error"]:
                lines.append(f"- native error: {row['native']['error']}")
        if not s["passed"]:
            lines.append(f"- skill missing: {s['missing']}")
            lines.append(f"- skill forbidden: {s['forbidden_hits']}")
            if row["skill"]["error"]:
                lines.append(f"- skill error: {row['skill']['error']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    root = _project_root()
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from openclaw_capture_workflow.accuracy_eval import load_eval_cases
    from openclaw_capture_workflow.config import AppConfig, SummarizerConfig
    from openclaw_capture_workflow.extractor import EvidenceExtractor
    from openclaw_capture_workflow.models import IngestRequest
    from openclaw_capture_workflow.summarizer import OpenAICompatibleSummarizer

    parser = argparse.ArgumentParser(description="Compare native vs summarize skill")
    parser.add_argument("--config", default=str(root / "config.json"))
    parser.add_argument("--cases", default=str(root / "scripts" / "accuracy_eval_cases.multitype.json"))
    parser.add_argument("--native-model", default="gpt-4o-mini")
    parser.add_argument("--skill-model", default="openai/gpt-4o-mini")
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--output-dir", default=str(root / "state" / "reports"))
    args = parser.parse_args()

    cfg = AppConfig.load(args.config)
    base_dir = Path(args.config).resolve().parent
    state_dir = cfg.ensure_state_dirs(base_dir)
    extractor = EvidenceExtractor(cfg, state_dir / "artifacts")
    native_cfg = SummarizerConfig(
        api_base_url=cfg.summarizer.api_base_url,
        api_key=cfg.summarizer.api_key,
        model=args.native_model.strip() or cfg.summarizer.model,
        timeout_seconds=cfg.summarizer.timeout_seconds,
    )
    native = OpenAICompatibleSummarizer(native_cfg)

    cases = load_eval_cases(args.cases)
    if args.max_cases > 0:
        cases = cases[: int(args.max_cases)]

    results: list[dict[str, Any]] = []
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for idx, case in enumerate(cases):
        rid = f"cmp-{case.case_id}-{stamp}-{idx+1}"
        ingest = IngestRequest(
            chat_id="-1",
            reply_to_message_id="1",
            request_id=rid,
            source_kind=case.source_kind,
            source_url=case.source_url,
            raw_text=case.raw_text or case.source_url,
            image_refs=list(case.image_refs),
            platform_hint=case.platform_hint,
            dry_run=True,
            video_probe_seconds=case.video_probe_seconds,
            force_full_video=case.force_full_video,
        )
        evidence = extractor.extract(ingest)

        native_text = ""
        native_err = ""
        try:
            native_summary = native.summarize(evidence)
            native_text = _native_summary_text(native_summary)
        except Exception as exc:
            native_err = str(exc)

        skill_text, skill_err = _run_skill_summary(
            evidence_text=evidence.text,
            model=args.skill_model,
            timeout_sec=max(30, int(cfg.summarizer.timeout_seconds)),
        )

        native_eval = _evaluate_text(native_text, case.expect)
        skill_eval = _evaluate_text(skill_text, case.expect)

        winner = "tie"
        if native_eval["score"] > skill_eval["score"]:
            winner = "native"
        elif skill_eval["score"] > native_eval["score"]:
            winner = "skill"

        results.append(
            {
                "case_id": case.case_id,
                "source_kind": case.source_kind,
                "evidence_chars": len(_norm_text(evidence.text)),
                "native": {
                    "error": native_err or None,
                    "eval": native_eval,
                    "text_preview": native_text[:1000],
                },
                "skill": {
                    "error": skill_err or None,
                    "eval": skill_eval,
                    "text_preview": skill_text[:1000],
                    "command": " ".join(shlex.quote(x) for x in [
                        "summarize",
                        "<evidence.txt>",
                        "--length",
                        "short",
                        "--max-output-tokens",
                        "700",
                        "--model",
                        args.skill_model,
                    ]),
                },
                "winner": winner,
                "expect": asdict(case.expect),
            }
        )

    def _agg(side: str) -> dict[str, Any]:
        total = len(results)
        pass_count = sum(1 for item in results if item[side]["eval"]["passed"])
        avg_score = (sum(float(item[side]["eval"]["score"]) for item in results) / total) if total else 0.0
        return {
            "pass_count": pass_count,
            "total_count": total,
            "pass_rate": round((pass_count / total) if total else 0.0, 4),
            "avg_score": round(avg_score, 4),
        }

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": str(Path(args.config).resolve()),
        "cases": str(Path(args.cases).resolve()),
        "case_count": len(results),
        "native_model": native_cfg.model,
        "skill_model": args.skill_model,
        "overall": {
            "native": _agg("native"),
            "skill": _agg("skill"),
            "winner_stats": {
                "native": sum(1 for item in results if item["winner"] == "native"),
                "skill": sum(1 for item in results if item["winner"] == "skill"),
                "tie": sum(1 for item in results if item["winner"] == "tie"),
            },
        },
        "results": results,
    }

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"compare_native_vs_skill_{stamp}.json"
    md_path = out_dir / f"compare_native_vs_skill_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")

    print(f"case_count={report['case_count']}")
    print(
        "native: pass={}/{} rate={} avg={}".format(
            report["overall"]["native"]["pass_count"],
            report["overall"]["native"]["total_count"],
            report["overall"]["native"]["pass_rate"],
            report["overall"]["native"]["avg_score"],
        )
    )
    print(
        "skill: pass={}/{} rate={} avg={}".format(
            report["overall"]["skill"]["pass_count"],
            report["overall"]["skill"]["total_count"],
            report["overall"]["skill"]["pass_rate"],
            report["overall"]["skill"]["avg_score"],
        )
    )
    print(
        "winner_stats={}".format(
            json.dumps(report["overall"]["winner_stats"], ensure_ascii=False)
        )
    )
    print(f"report_json={json_path}")
    print(f"report_markdown={md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
