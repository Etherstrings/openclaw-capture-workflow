"""Thin HTTP wrapper around the URL analyzer."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .analyzer import analyze_url
from .config import AppConfig


def create_app(config_path: str):
    try:
        from fastapi import Body, FastAPI, HTTPException
    except ImportError as exc:
        raise RuntimeError("fastapi is not installed") from exc

    config = AppConfig.load(config_path)
    base_dir = Path(config_path).resolve().parent
    state_dir = config.ensure_state_dirs(base_dir)

    app = FastAPI(title="OpenClaw URL Analyzer", version="0.1.0")

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "service": "url-analyzer",
            "browser_backend": config.analysis.browser_backend,
        }

    @app.post("/analyze-url")
    def analyze_url_endpoint(payload: dict = Body(...)) -> dict:
        url = str(payload.get("url", "")).strip()
        requested_output_lang = str(payload.get("requested_output_lang", "zh-CN")).strip() or "zh-CN"
        if not url:
            raise HTTPException(status_code=422, detail="url is required")
        try:
            outcome = analyze_url(
                url=url,
                requested_output_lang=requested_output_lang,
                config=config,
                state_dir=state_dir,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        result = outcome.document.to_dict()
        result["warnings"] = list(outcome.warnings)
        return result

    return app


def run_api(config_path: str, host: str = "127.0.0.1", port: int = 8775) -> int:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("uvicorn is not installed") from exc
    app = create_app(config_path)
    uvicorn.run(app, host=host, port=port)
    return 0
