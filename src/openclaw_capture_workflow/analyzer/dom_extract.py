"""HTML extraction for URL understanding."""

from __future__ import annotations

from html.parser import HTMLParser
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse, urljoin

from .models import CollectedImage, CollectedVideo, ExtractedContent, ImageResult, RenderResult, SectionResult, TableResult, VideoResult


NOISE_TAGS = {"script", "style", "nav", "footer", "form", "noscript"}
NOISE_SELECTORS = [
    ".toctree-wrapper",
    ".sphinxsidebar",
    ".related",
    ".headerlink",
    ".js-header-wrapper",
    ".js-repo-nav",
    ".Layout-sidebar",
]
PRIMARY_CONTENT_SELECTORS = [
    '[data-testid="readme"]',
    ".markdown-body",
    "article.markdown-body",
    '[role="main"]',
    ".body",
    ".document",
    "article",
    "main",
    "body",
]
NOISE_PHRASES = {
    "uh oh! there was an error while loading",
    "additional navigation options",
    "skip to content",
    "public notifications",
    "you must be signed in to change notification settings",
    "navigation menu toggle navigation",
}


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _paragraph_summary(text: str, limit: int = 180) -> str:
    cleaned = _clean_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def _detect_video_provider(url: str) -> Optional[str]:
    host = urlparse(url).netloc.lower()
    if not host:
        return None
    for key in ("youtube", "youtu.be", "bilibili", "vimeo", "xiaohongshu", "loom"):
        if key in host:
            return key
    return host


def _is_noise_text(text: str) -> bool:
    lowered = _clean_text(text).lower()
    if not lowered:
        return True
    return any(phrase in lowered for phrase in NOISE_PHRASES)


def _is_decorative_image(src: str, alt: Optional[str]) -> bool:
    lowered_src = src.lower()
    lowered_alt = (alt or "").strip().lower()
    if any(token in lowered_src for token in ["avatar", "gravatar", "githubassets", "octicon", "favicon", "icon", "logo"]):
        return True
    if lowered_alt in {"avatar", "icon", "logo"}:
        return True
    return False


class _FallbackHTMLCollector(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.tag_stack: List[str] = []
        self.title_parts: List[str] = []
        self.text_blocks: List[str] = []
        self.sections: List[SectionResult] = []
        self.images: List[ImageResult] = []
        self.videos: List[VideoResult] = []
        self.tables: List[TableResult] = []
        self._buffer: List[str] = []
        self._current_heading: Optional[str] = None
        self._current_level: Optional[int] = None
        self._current_table: Optional[Dict[str, object]] = None
        self._current_row: Optional[List[str]] = None
        self._current_cell: List[str] = []
        self._current_cell_tag: Optional[str] = None
        self._ignore_depth = 0
        self._open_video_poster: Optional[str] = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = dict(attrs)
        self.tag_stack.append(tag)
        if tag in NOISE_TAGS:
            self._ignore_depth += 1
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "article", "section", "main", "div"}:
            self._flush_buffer(force=False)

        if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
            self._current_level = int(tag[1])

        if tag == "img":
            src = _clean_text(attrs_dict.get("src", ""))
            if src:
                self.images.append(
                    ImageResult(
                        src=urljoin(self.base_url, src),
                        alt=_clean_text(attrs_dict.get("alt", "")) or None,
                        caption=None,
                        context=None,
                    )
                )

        if tag == "video":
            src = _clean_text(attrs_dict.get("src", ""))
            poster = _clean_text(attrs_dict.get("poster", ""))
            self._open_video_poster = urljoin(self.base_url, poster) if poster else None
            if src:
                absolute = urljoin(self.base_url, src)
                self.videos.append(
                    VideoResult(
                        src=absolute,
                        poster=self._open_video_poster,
                        provider=_detect_video_provider(absolute),
                    )
                )

        if tag == "source":
            src = _clean_text(attrs_dict.get("src", ""))
            if src and self.tag_stack[:-1] and self.tag_stack[-2] == "video":
                absolute = urljoin(self.base_url, src)
                self.videos.append(
                    VideoResult(
                        src=absolute,
                        poster=self._open_video_poster,
                        provider=_detect_video_provider(absolute),
                    )
                )

        if tag == "iframe":
            src = _clean_text(attrs_dict.get("src", ""))
            if src and any(token in src.lower() for token in ["youtube", "youtu.be", "bilibili", "vimeo", "loom"]):
                absolute = urljoin(self.base_url, src)
                self.videos.append(
                    VideoResult(
                        src=absolute,
                        poster=None,
                        provider=_detect_video_provider(absolute),
                    )
                )

        if tag == "table":
            self._flush_buffer(force=False)
            self._current_table = {"caption": None, "headers": [], "rows": []}
        elif tag == "caption" and self._current_table is not None:
            self._current_cell = []
            self._current_cell_tag = "caption"
        elif tag == "tr" and self._current_table is not None:
            self._current_row = []
        elif tag in {"th", "td"} and self._current_table is not None:
            self._current_cell = []
            self._current_cell_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag in NOISE_TAGS and self._ignore_depth > 0:
            self._ignore_depth -= 1
        if self._ignore_depth > 0:
            if self.tag_stack:
                self.tag_stack.pop()
            return

        if tag == "title":
            self._flush_buffer(force=True)

        if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
            heading = _clean_text(" ".join(self._buffer))
            self._buffer = []
            if heading:
                if self._current_heading:
                    self.sections.append(
                        SectionResult(
                            heading=self._current_heading,
                            level=self._current_level,
                            content="",
                        )
                    )
                self._current_heading = heading
                self._current_level = int(tag[1])
                self.text_blocks.append(heading)
        elif tag in {"p", "li"}:
            block = _clean_text(" ".join(self._buffer))
            self._buffer = []
            if block:
                self.text_blocks.append(block)
                if self._current_heading:
                    self._append_to_section(block)
        elif tag == "caption" and self._current_table is not None and self._current_cell_tag == "caption":
            self._current_table["caption"] = _clean_text(" ".join(self._current_cell)) or None
            self._current_cell = []
            self._current_cell_tag = None
        elif tag in {"th", "td"} and self._current_table is not None and self._current_cell_tag == tag:
            value = _clean_text(" ".join(self._current_cell))
            if value:
                if tag == "th":
                    headers = self._current_table.get("headers", [])
                    if isinstance(headers, list):
                        headers.append(value)
                elif self._current_row is not None:
                    self._current_row.append(value)
            self._current_cell = []
            self._current_cell_tag = None
        elif tag == "tr" and self._current_table is not None and self._current_row is not None:
            if self._current_row:
                rows = self._current_table.get("rows", [])
                if isinstance(rows, list):
                    rows.append(list(self._current_row))
            self._current_row = None
        elif tag == "table" and self._current_table is not None:
            self.tables.append(
                TableResult(
                    caption=self._current_table.get("caption") if isinstance(self._current_table.get("caption"), str) else None,
                    headers=list(self._current_table.get("headers", [])) if isinstance(self._current_table.get("headers"), list) else [],
                    rows=list(self._current_table.get("rows", [])) if isinstance(self._current_table.get("rows"), list) else [],
                )
            )
            self._current_table = None
        elif tag == "video":
            self._open_video_poster = None

        if self.tag_stack:
            self.tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._ignore_depth > 0:
            return
        text = _clean_text(data)
        if not text:
            return
        if self.tag_stack and self.tag_stack[-1] == "title":
            self.title_parts.append(text)
            return
        if self._current_cell_tag in {"caption", "th", "td"}:
            self._current_cell.append(text)
            return
        self._buffer.append(text)

    def close(self) -> None:
        self._flush_buffer(force=False)
        super().close()

    def _append_to_section(self, block: str) -> None:
        if not self.sections or self.sections[-1].heading != self._current_heading:
            self.sections.append(
                SectionResult(
                    heading=self._current_heading,
                    level=self._current_level,
                    content=block,
                )
            )
            return
        current = self.sections[-1].content
        self.sections[-1].content = f"{current}\n\n{block}" if current else block

    def _flush_buffer(self, force: bool) -> None:
        if not self._buffer:
            return
        if force and self.tag_stack and self.tag_stack[-1] == "title":
            self.title_parts.append(_clean_text(" ".join(self._buffer)))
        self._buffer = []


def _load_bs4():
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return None
    return BeautifulSoup


def _extract_with_bs4(render_result: RenderResult, max_images: int, max_videos: int, max_tables: int) -> ExtractedContent:
    BeautifulSoup = _load_bs4()
    if BeautifulSoup is None:
        raise RuntimeError("BeautifulSoup is not installed")
    soup = BeautifulSoup(render_result.html, "html.parser")
    for tag_name in NOISE_TAGS:
        for node in soup.find_all(tag_name):
            node.decompose()
    for selector in NOISE_SELECTORS:
        for node in soup.select(selector):
            node.decompose()

    title = _clean_text(render_result.title or (soup.title.get_text(" ", strip=True) if soup.title else "")) or "Untitled page"
    main_node = None
    for selector in PRIMARY_CONTENT_SELECTORS:
        main_node = soup.select_one(selector)
        if main_node is not None:
            break
    if main_node is None:
        main_node = soup
    sections: List[SectionResult] = []
    text_blocks: List[str] = []
    current_heading: Optional[str] = None
    current_level: Optional[int] = None
    current_chunks: List[str] = []

    def flush_section() -> None:
        nonlocal current_heading, current_level, current_chunks
        if current_heading and current_chunks:
            sections.append(
                SectionResult(
                    heading=current_heading,
                    level=current_level,
                    content="\n\n".join(chunk for chunk in current_chunks if chunk),
                )
            )
        current_heading = None
        current_level = None
        current_chunks = []

    for node in main_node.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"], recursive=True):
        text = _clean_text(node.get_text(" ", strip=True))
        if not text:
            continue
        if _is_noise_text(text):
            continue
        if node.name and node.name.startswith("h"):
            flush_section()
            current_heading = text
            current_level = int(node.name[1]) if node.name[1].isdigit() else None
            text_blocks.append(text)
            continue
        text_blocks.append(text)
        if current_heading:
            current_chunks.append(text)
    flush_section()

    images: List[CollectedImage] = []
    for node in main_node.find_all("img", recursive=True):
        src = _clean_text(node.get("src", ""))
        if not src:
            continue
        alt = _clean_text(node.get("alt", "")) or None
        absolute = urljoin(render_result.final_url, src)
        if _is_decorative_image(absolute, alt):
            continue
        images.append(
            CollectedImage(
                result=ImageResult(
                    src=absolute,
                    alt=alt,
                    caption=None,
                    context=None,
                )
            )
        )

    videos: List[CollectedVideo] = []
    for node in main_node.find_all(["video", "iframe"], recursive=True):
        if node.name == "video":
            src = _clean_text(node.get("src", ""))
            if not src:
                source = node.find("source")
                src = _clean_text(source.get("src", "")) if source else ""
            if not src:
                continue
            absolute = urljoin(render_result.final_url, src)
            videos.append(
                CollectedVideo(
                    result=VideoResult(
                        src=absolute,
                        poster=urljoin(render_result.final_url, _clean_text(node.get("poster", "")))
                        if _clean_text(node.get("poster", ""))
                        else None,
                        provider=_detect_video_provider(absolute),
                    )
                )
            )
        elif node.name == "iframe":
            src = _clean_text(node.get("src", ""))
            if not src or not any(token in src.lower() for token in ["youtube", "youtu.be", "bilibili", "vimeo", "loom"]):
                continue
            absolute = urljoin(render_result.final_url, src)
            videos.append(
                CollectedVideo(
                    result=VideoResult(
                        src=absolute,
                        poster=None,
                        provider=_detect_video_provider(absolute),
                    )
                )
            )

    tables: List[TableResult] = []
    for table in main_node.find_all("table", recursive=True):
        caption = table.find("caption")
        headers = [
            _clean_text(cell.get_text(" ", strip=True))
            for cell in table.find_all("th")
            if _clean_text(cell.get_text(" ", strip=True))
        ]
        rows: List[List[str]] = []
        for row in table.find_all("tr"):
            cells = [
                _clean_text(cell.get_text(" ", strip=True))
                for cell in row.find_all(["td", "th"])
                if _clean_text(cell.get_text(" ", strip=True))
            ]
            if cells:
                rows.append(cells)
        if headers or rows:
            if len(headers) <= 1 and len(rows) <= 1:
                continue
            tables.append(
                TableResult(
                    caption=_clean_text(caption.get_text(" ", strip=True)) if caption else None,
                    headers=headers,
                    rows=rows,
                )
            )

    sections = [section for section in sections if section.content.strip() and not _is_noise_text(section.heading or "")]
    text_blocks = [block for block in text_blocks if not _is_noise_text(block)]

    return ExtractedContent(
        title=title,
        main_text="\n\n".join(text_blocks).strip(),
        sections=sections,
        images=images[:max_images],
        videos=videos[:max_videos],
        tables=tables[:max_tables],
    )


def _extract_with_fallback_parser(render_result: RenderResult, max_images: int, max_videos: int, max_tables: int) -> ExtractedContent:
    collector = _FallbackHTMLCollector(render_result.final_url)
    collector.feed(render_result.html)
    collector.close()
    title = _clean_text(" ".join(collector.title_parts)) or _clean_text(render_result.title) or "Untitled page"
    images = [CollectedImage(result=item) for item in collector.images[:max_images]]
    videos = [CollectedVideo(result=item) for item in collector.videos[:max_videos]]
    return ExtractedContent(
        title=title,
        main_text="\n\n".join(collector.text_blocks).strip(),
        sections=collector.sections,
        images=images,
        videos=videos,
        tables=collector.tables[:max_tables],
    )


def extract_content(render_result: RenderResult, max_images: int, max_videos: int, max_tables: int) -> ExtractedContent:
    try:
        extracted = _extract_with_bs4(render_result, max_images=max_images, max_videos=max_videos, max_tables=max_tables)
    except Exception:
        extracted = _extract_with_fallback_parser(
            render_result,
            max_images=max_images,
            max_videos=max_videos,
            max_tables=max_tables,
        )

    if not extracted.sections and extracted.main_text:
        extracted.sections = [
            SectionResult(
                heading=extracted.title,
                level=1,
                content=_paragraph_summary(extracted.main_text, limit=1200),
            )
        ]

    if not extracted.main_text:
        text_hint = _clean_text(render_result.text_hint)
        if text_hint:
            extracted.main_text = text_hint
            if not extracted.sections:
                extracted.sections = [SectionResult(heading=extracted.title, level=1, content=text_hint)]
    return extracted
