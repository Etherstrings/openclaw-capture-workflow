"""Simple JSON-file job persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .models import JobRecord


class JobStore:
    def __init__(self, jobs_dir: Path) -> None:
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def save(self, job: JobRecord) -> None:
        path = self._path(job.job_id)
        tmp_path = self.jobs_dir / f".{job.job_id}.{os.getpid()}.tmp"
        payload = json.dumps(job.to_dict(), ensure_ascii=False, indent=2)
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(path)

    def load(self, job_id: str) -> Optional[JobRecord]:
        path = self._path(job_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        job = JobRecord(**data)
        job.ensure_tracking_fields()
        return job
