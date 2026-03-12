"""HTTP server for local ingestion."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
import json
import subprocess
from typing import Callable
from urllib.parse import parse_qs, quote, urlparse

from .models import IngestRequest
from .obsidian import ObsidianWriter
from .processor import WorkflowProcessor
from .storage import JobStore
from .stock_pipeline import StockPipelineTrigger
from .telegram import TelegramNotifier


class RequestHandler(BaseHTTPRequestHandler):
    processor: WorkflowProcessor
    job_store: JobStore
    obsidian_writer: ObsidianWriter
    stock_trigger: StockPipelineTrigger
    telegram_notifier: TelegramNotifier

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if parsed.path == "/open":
            params = parse_qs(parsed.query)
            raw_path = params.get("path", [""])[0]
            if not raw_path:
                self._text(HTTPStatus.BAD_REQUEST, "missing path")
                return
            obsidian_uri = self.obsidian_writer._obsidian_uri(raw_path)
            html = self._open_page(raw_path, obsidian_uri)
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path.startswith("/jobs/"):
            job_id = parsed.path.split("/")[-1]
            job = self.job_store.load(job_id)
            if not job:
                self._json(HTTPStatus.NOT_FOUND, {"error": "job not found"})
                return
            self._json(HTTPStatus.OK, job.to_dict())
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/stock-trigger":
            self._handle_stock_trigger()
            return
        if self.path != "/ingest":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw)
            ingest = IngestRequest.from_dict(payload)
            job = self.processor.enqueue(ingest)
            self._json(
                HTTPStatus.ACCEPTED,
                {
                    "job_id": job.job_id,
                    "status": job.status,
                    "message": "已收到，开始处理",
                },
            )
        except Exception as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _handle_stock_trigger(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw) if raw else {}
            chat_id = str(payload["chat_id"])
            reply_to_message_id = payload.get("reply_to_message_id")
            try:
                reply_to_message_id = int(reply_to_message_id) if reply_to_message_id is not None else None
            except (TypeError, ValueError):
                reply_to_message_id = None
            action = str(payload.get("action", "trigger"))
            mode = str(payload.get("mode", "full"))

            if action == "inspect":
                result = self.stock_trigger.inspect()
            else:
                result = self.stock_trigger.trigger(mode=mode)

            self.telegram_notifier.send_text(
                chat_id=chat_id,
                text=result.message,
                reply_to_message_id=reply_to_message_id,
            )
            self._json(
                HTTPStatus.ACCEPTED,
                {
                    "ok": True,
                    "message": result.message,
                    "run_url": result.run_url,
                    "status": result.status,
                    "mode": result.mode,
                },
            )
        except subprocess.CalledProcessError as exc:  # type: ignore[name-defined]
            stderr = (exc.stderr or "").strip()
            self._json(HTTPStatus.BAD_GATEWAY, {"error": stderr or str(exc)})
        except Exception as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, status: HTTPStatus, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _open_page(self, note_path: str, obsidian_uri: str) -> str:
        safe_path = escape(note_path)
        safe_uri = escape(obsidian_uri, quote=True)
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Open In Obsidian</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, sans-serif;
      background: #10131a;
      color: #f3f6fb;
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
    }}
    .card {{
      width: min(680px, calc(100vw - 32px));
      background: #171c26;
      border: 1px solid #2b3344;
      border-radius: 20px;
      padding: 28px;
      box-sizing: border-box;
    }}
    h1 {{ margin: 0 0 12px; font-size: 24px; }}
    p {{ line-height: 1.6; color: #cad3e2; }}
    code {{
      display: block;
      padding: 12px;
      border-radius: 12px;
      background: #0f141c;
      color: #8ee7a7;
      overflow-wrap: anywhere;
    }}
    .actions {{ margin-top: 18px; display: flex; gap: 12px; flex-wrap: wrap; }}
    a.button {{
      display: inline-block;
      padding: 12px 16px;
      border-radius: 12px;
      background: #5aa9ff;
      color: #08111f;
      text-decoration: none;
      font-weight: 700;
    }}
    a.subtle {{
      color: #9ac8ff;
      text-decoration: none;
      padding: 12px 0;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>打开 Obsidian 笔记</h1>
    <p>如果浏览器没有自动跳转，请点击下面的按钮。</p>
    <code>{safe_path}</code>
    <div class="actions">
      <a class="button" href="{safe_uri}">在 Obsidian 中打开</a>
    </div>
  </div>
  <script>
    window.location.href = "{safe_uri}";
  </script>
</body>
</html>"""


def build_server(host: str, port: int, processor: WorkflowProcessor, job_store: JobStore) -> ThreadingHTTPServer:
    handler: Callable[..., RequestHandler] = RequestHandler
    handler.processor = processor
    handler.job_store = job_store
    handler.obsidian_writer = processor.writer
    handler.stock_trigger = StockPipelineTrigger()
    handler.telegram_notifier = processor.notifier
    return ThreadingHTTPServer((host, port), handler)
