"""Trigger and inspect the remote daily stock analysis GitHub workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import subprocess
from typing import Any


@dataclass
class StockTriggerResult:
    message: str
    run_url: str | None = None
    run_id: int | None = None
    status: str | None = None
    mode: str = "full"


class StockPipelineTrigger:
    def __init__(self, repo: str = "Etherstrings/daily_stock_analysis", workflow: str = "daily_analysis.yml") -> None:
        self.repo = repo
        self.workflow = workflow

    def trigger(self, mode: str = "full") -> StockTriggerResult:
        mode = mode if mode in {"full", "market-only", "stocks-only"} else "full"
        self._run(
            [
                "gh",
                "workflow",
                "run",
                self.workflow,
                "--repo",
                self.repo,
                "-f",
                f"mode={mode}",
            ]
        )
        run = self._latest_run()
        if run:
            status = run.get("status")
            url = run.get("url")
            database_id = run.get("databaseId")
            return StockTriggerResult(
                message=self._trigger_message(status=status, url=url),
                run_url=url,
                run_id=database_id,
                status=status,
                mode=mode,
            )
        return StockTriggerResult(
            message="已触发每日股票分析流水线。请稍后在 GitHub Actions 中查看运行状态。",
            mode=mode,
        )

    def inspect(self) -> StockTriggerResult:
        run = self._latest_run()
        if not run:
            return StockTriggerResult(message="未找到最近的每日股票分析运行记录。")
        status = run.get("status")
        url = run.get("url")
        database_id = run.get("databaseId")
        return StockTriggerResult(
            message=self._inspect_message(status=status, url=url),
            run_url=url,
            run_id=database_id,
            status=status,
        )

    def ensure_running(self, mode: str = "full") -> StockTriggerResult:
        run = self._latest_run()
        if not run:
            return self.trigger(mode=mode)

        status = (run.get("status") or "").strip().lower()
        conclusion = (run.get("conclusion") or "").strip().lower()
        if status in {"queued", "in_progress", "pending", "requested", "waiting"}:
            return StockTriggerResult(
                message=self._already_running_message(status=status),
                run_url=run.get("url"),
                run_id=run.get("databaseId"),
                status=run.get("status"),
                mode=mode,
            )

        if conclusion in {"failure", "cancelled", "timed_out", "startup_failure", "action_required"}:
            return self.trigger(mode=mode)

        return StockTriggerResult(
            message=self._no_retrigger_needed_message(status=run.get("status"), conclusion=run.get("conclusion")),
            run_url=run.get("url"),
            run_id=run.get("databaseId"),
            status=run.get("status"),
            mode=mode,
        )

    def _latest_run(self) -> dict[str, Any] | None:
        output = self._run(
            [
                "gh",
                "run",
                "list",
                "--repo",
                self.repo,
                "--workflow",
                self.workflow,
                "--limit",
                "1",
                "--json",
                "databaseId,status,conclusion,createdAt,updatedAt,url,displayTitle,event",
            ]
        )
        runs = json.loads(output)
        if not runs:
            return None
        return runs[0]

    def _run(self, args: list[str]) -> str:
        completed = subprocess.run(args, check=True, capture_output=True, text=True)
        return completed.stdout.strip()

    def _trigger_message(self, *, status: str | None, url: str | None) -> str:
        lines = ["已为您触发今日股票分析流水线。"]
        if status:
            lines.append(f"当前状态：{status}")
        return "\n".join(lines)

    def _inspect_message(self, *, status: str | None, url: str | None) -> str:
        lines = ["已检查每日股票分析流水线状态。"]
        if status:
            lines.append(f"当前状态：{status}")
        lines.append(f"检查时间：{datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')}")
        return "\n".join(lines)

    def _already_running_message(self, *, status: str | None) -> str:
        lines = ["每日股票分析流水线已在运行中。"]
        if status:
            lines.append(f"当前状态：{status}")
        lines.append("无需再次触发。")
        return "\n".join(lines)

    def _no_retrigger_needed_message(self, *, status: str | None, conclusion: str | None) -> str:
        lines = ["已检查每日股票分析流水线状态。"]
        if status:
            lines.append(f"当前状态：{status}")
        if conclusion:
            lines.append(f"最近结果：{conclusion}")
        lines.append("无需再次触发。")
        return "\n".join(lines)
