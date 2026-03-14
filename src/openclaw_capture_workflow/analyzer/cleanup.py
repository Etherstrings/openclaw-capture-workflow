"""Temporary artifact helpers for analyzer jobs."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Tuple


def create_job_temp_dir(root: Path) -> Tuple[str, Path]:
    job_id = str(uuid.uuid4())
    job_dir = root / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    return job_id, job_dir


def cleanup_job_temp_dir(path: Path) -> None:
    if not path.exists():
        return
    shutil.rmtree(path, ignore_errors=True)

