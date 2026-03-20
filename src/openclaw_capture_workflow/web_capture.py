"""Lightweight layered web capture inspired by extractor-first strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
from typing import Callable
from urllib import request as urlrequest


@dataclass
class WebCaptureLayer:
    name: str
    status: str
    title: str | None = None
    text: str = ""
    provider: str = ""
    reason: str = ""


@dataclass
class WebCaptureResult:
    title: str | None
    merged_text: str
    layers: list[WebCaptureLayer] = field(default_factory=list)


class _VisibleTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_stack: list[str] = []
        self._chunks: list[str] = []
        self._title_chunks: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_stack.append(tag)
        if tag == "title":
            self._in_title = True
        if tag in {"p", "article", "section", "div", "li", "h1", "h2", "h3", "h4", "blockquote", "pre"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if self._skip_stack and self._skip_stack[-1] == tag:
            self._skip_stack.pop()
        if tag == "title":
            self._in_title = False
        if tag in {"p", "article", "section", "div", "li", "h1", "h2", "h3", "h4", "blockquote", "pre"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._skip_stack:
            return
        text = re.sub(r"\s+", " ", data or "").strip()
        if not text:
            return
        if self._in_title:
            self._title_chunks.append(text)
        self._chunks.append(text)

    @property
    def title(self) -> str | None:
        value = re.sub(r"\s+", " ", " ".join(self._title_chunks)).strip()
        return value or None

    @property
    def text(self) -> str:
        lines: list[str] = []
        seen: set[str] = set()
        for raw in "".join(self._chunks).splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if len(line) < 3:
                continue
            if line in seen:
                continue
            seen.add(line)
            lines.append(line)
        return "\n".join(lines[:120]).strip()


def _sanitize_text(text: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line) < 3:
            continue
        lowered = line.lower()
        if line in seen:
            continue
        if any(token in lowered for token in ["cookie", "privacy", "terms", "copyright", "all rights reserved"]):
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines[:120]).strip()


class WebCaptureEngine:
    def capture(
        self,
        url: str,
        *,
        rendered_fetch: Callable[[str], tuple[str | None, str]] | None = None,
    ) -> WebCaptureResult:
        layers: list[WebCaptureLayer] = []
        html_title = None
        html_text = ""
        try:
            req = urlrequest.Request(url, headers={"User-Agent": "Mozilla/5.0 OpenClawCaptureWorkflow/0.1"})
            with urlrequest.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            parser = _VisibleTextHTMLParser()
            parser.feed(body)
            html_title = parser.title
            html_text = _sanitize_text(parser.text)
            layers.append(
                WebCaptureLayer(
                    name="web_html",
                    status="ok" if html_text else "empty",
                    title=html_title,
                    text=html_text,
                    provider="stdlib_html_parser",
                )
            )
        except Exception as exc:
            layers.append(
                WebCaptureLayer(
                    name="web_html",
                    status="failed",
                    reason=str(exc),
                    provider="stdlib_html_parser",
                )
            )

        render_title = None
        render_text = ""
        if rendered_fetch is not None:
            try:
                render_title, render_text = rendered_fetch(url)
                render_text = _sanitize_text(render_text)
                layers.append(
                    WebCaptureLayer(
                        name="browser_render",
                        status="ok" if render_text else "empty",
                        title=render_title,
                        text=render_text,
                        provider="browser_render",
                    )
                )
            except Exception as exc:
                layers.append(
                    WebCaptureLayer(
                        name="browser_render",
                        status="failed",
                        reason=str(exc),
                        provider="browser_render",
                    )
                )

        merged_blocks = [layer.text for layer in layers if layer.status == "ok" and layer.text.strip()]
        title = render_title or html_title
        return WebCaptureResult(
            title=title,
            merged_text="\n\n".join(merged_blocks).strip(),
            layers=layers,
        )
