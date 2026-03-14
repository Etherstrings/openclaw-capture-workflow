"""Browser backends for URL rendering."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
from pathlib import Path
from typing import Dict, Protocol
from urllib import request as urlrequest

from .models import RenderResult


class BrowserBackend(Protocol):
    def render(self, url: str, temp_dir: Path, timeout_seconds: int) -> RenderResult:
        ...


@dataclass
class PlaywrightBackend:
    screenshot_name: str = "page.png"

    def render(self, url: str, temp_dir: Path, timeout_seconds: int) -> RenderResult:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("playwright is not installed") from exc

        screenshot_path = temp_dir / self.screenshot_name
        metadata: Dict[str, object] = {}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_seconds, 10) * 1000)
                except PlaywrightTimeoutError:
                    metadata["networkidle_timeout"] = True
                page.screenshot(path=str(screenshot_path), full_page=False)
                text_hint = page.locator("body").inner_text(timeout=2000)
                return RenderResult(
                    requested_url=url,
                    final_url=page.url,
                    title=page.title(),
                    html=page.content(),
                    screenshot_path=screenshot_path if screenshot_path.exists() else None,
                    text_hint=(text_hint or "").strip(),
                    metadata=metadata,
                )
            finally:
                page.close()
                browser.close()


@dataclass
class PinchTabBackend:
    base_url: str = ""

    def _http_json(self, method: str, path: str, payload: dict | None = None) -> dict:
        req = urlrequest.Request(
            url=self.base_url.rstrip("/") + path,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urlrequest.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}

    def _http_bytes(self, path: str) -> bytes:
        req = urlrequest.Request(
            url=self.base_url.rstrip("/") + path,
            method="GET",
        )
        with urlrequest.urlopen(req, timeout=30) as resp:
            return resp.read()

    def _ensure_instance(self) -> None:
        try:
            health = self._http_json("GET", "/health")
            if str(health.get("status", "")).lower() == "ok":
                return
        except Exception:
            pass
        started = self._http_json("POST", "/instances/start", {"mode": "headless"})
        instance_id = str(started.get("id", "")).strip()
        if not instance_id:
            raise RuntimeError("PinchTab failed to start an instance")

    def _text_to_html(self, title: str, text: str) -> str:
        paragraphs = [segment.strip() for segment in (text or "").splitlines() if segment.strip()]
        body = "\n".join(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs)
        safe_title = escape(title)
        return f"<html><head><title>{safe_title}</title></head><body><main><h1>{safe_title}</h1>{body}</main></body></html>"

    def render(self, url: str, temp_dir: Path, timeout_seconds: int) -> RenderResult:
        if not self.base_url.strip():
            raise RuntimeError("pinchtab_base_url is not configured")
        self._ensure_instance()
        navigation = self._http_json(
            "POST",
            "/navigate",
            {"url": url, "timeout": timeout_seconds * 1000},
        )
        tab_id = str(navigation.get("tabId", "")).strip()
        if not tab_id:
            raise RuntimeError("PinchTab did not return a tabId")
        text_payload = self._http_json("GET", f"/tabs/{tab_id}/text?raw=true")
        title = str(text_payload.get("title") or navigation.get("title") or "").strip() or "Untitled page"
        text = str(text_payload.get("text", "")).strip()
        screenshot_path = temp_dir / "pinchtab-page.png"
        screenshot_path.write_bytes(self._http_bytes(f"/tabs/{tab_id}/screenshot?raw=true"))
        return RenderResult(
            requested_url=url,
            final_url=str(text_payload.get("url") or navigation.get("url") or url).strip(),
            title=title,
            html=self._text_to_html(title, text),
            screenshot_path=screenshot_path if screenshot_path.exists() else None,
            text_hint=text,
            metadata={"backend": "pinchtab", "tab_id": tab_id},
        )
