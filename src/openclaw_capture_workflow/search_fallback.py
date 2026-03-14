"""Search enrichment using the OpenClaw browser CLI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote_plus, urlparse

from .analyzer.models import StructuredDocument
from .iterative_cases import RecognitionCase


DEFAULT_SEARCH_TEMPLATE = "https://duckduckgo.com/html/?q={query}"


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _tokenize_query_text(value: str) -> List[str]:
    text = _normalize_space(value)
    if not text:
        return []
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]{1,}|[\u4e00-\u9fff]{2,10}", text)
    deduped: List[str] = []
    for token in tokens:
        candidate = token.strip()
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered in {"github", "http", "https", "www", "com", "html"}:
            continue
        if candidate not in deduped:
            deduped.append(candidate)
        if len(deduped) >= 6:
            break
    return deduped


def build_site_query(case: RecognitionCase, document: StructuredDocument) -> str:
    host = urlparse(case.source_url or "").netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    title_tokens = _tokenize_query_text(document.title)
    summary_tokens = _tokenize_query_text(document.summary)
    terms = list(dict.fromkeys(title_tokens[:4] + summary_tokens[:3]))
    query = " ".join([f"site:{host}"] + terms) if host else " ".join(terms)
    return _normalize_space(query)


def build_broad_query(case: RecognitionCase, document: StructuredDocument) -> str:
    title_tokens = _tokenize_query_text(document.title)
    summary_tokens = _tokenize_query_text(document.summary)
    label_tokens = [token for label in case.labels for token in _tokenize_query_text(label)]
    terms = list(dict.fromkeys(title_tokens[:4] + summary_tokens[:4] + label_tokens[:2]))
    return _normalize_space(" ".join(terms))


@dataclass
class SearchResultItem:
    title: str
    url: str
    snippet: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FetchedPage:
    url: str
    title: str
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SearchEvidenceBundle:
    queries: List[str] = field(default_factory=list)
    results: List[SearchResultItem] = field(default_factory=list)
    fetched_pages: List[FetchedPage] = field(default_factory=list)
    evidence_text: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "queries": list(self.queries),
            "results": [item.to_dict() for item in self.results],
            "fetched_pages": [item.to_dict() for item in self.fetched_pages],
            "evidence_text": self.evidence_text,
            "warnings": list(self.warnings),
        }


def extract_search_results_from_snapshot(snapshot: str, limit: int = 5) -> List[SearchResultItem]:
    items: List[SearchResultItem] = []
    current_title = ""
    current_url = ""
    current_snippet_parts: List[str] = []

    def flush() -> None:
        nonlocal current_title, current_url, current_snippet_parts
        if current_title and current_url.startswith(("http://", "https://")):
            items.append(
                SearchResultItem(
                    title=current_title,
                    url=current_url,
                    snippet=_normalize_space(" ".join(current_snippet_parts))[:400],
                )
            )
        current_title = ""
        current_url = ""
        current_snippet_parts = []

    for raw_line in snapshot.splitlines():
        line = raw_line.strip()
        link_match = re.search(r'- link "([^"]+)"', line)
        if link_match:
            flush()
            current_title = _normalize_space(link_match.group(1))
            continue
        url_match = re.search(r"- /url:\s*(https?://\S+)", line)
        if url_match and not current_url:
            current_url = url_match.group(1).rstrip(")]}>,.;")
            continue
        text_match = re.search(r"- text:\s*(.+)$", line)
        if text_match and current_title:
            snippet = _normalize_space(text_match.group(1))
            if snippet:
                current_snippet_parts.append(snippet)
            continue
        generic_match = re.search(r"- generic\b.*?:\s*(.+)$", line)
        if generic_match and current_title:
            snippet = _normalize_space(generic_match.group(1))
            if snippet:
                current_snippet_parts.append(snippet)
    flush()

    deduped: List[SearchResultItem] = []
    seen_urls: set[str] = set()
    for item in items:
        if item.url in seen_urls:
            continue
        seen_urls.add(item.url)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


class OpenClawBrowserClient:
    def __init__(self, browser_profile: str = "") -> None:
        self.browser_profile = browser_profile.strip()

    def _run_json(self, *args: str) -> Dict[str, Any]:
        command = ["openclaw", "browser", *args, "--json"]
        if self.browser_profile:
            command.extend(["--browser-profile", self.browser_profile])
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def open_url(self, url: str) -> str:
        payload = self._run_json("open", url)
        target_id = str(payload.get("targetId", "")).strip()
        if not target_id:
            raise RuntimeError("browser open did not return targetId")
        return target_id

    def snapshot(self, target_id: str, limit: int = 250) -> str:
        payload = self._run_json("snapshot", "--target-id", target_id, "--limit", str(limit))
        snapshot = payload.get("snapshot", "")
        if not isinstance(snapshot, str):
            raise RuntimeError("browser snapshot returned invalid payload")
        return snapshot

    def evaluate(self, target_id: str, fn_source: str) -> Any:
        payload = self._run_json("evaluate", "--target-id", target_id, "--fn", fn_source)
        return payload.get("result")

    def close(self, target_id: str) -> None:
        try:
            self._run_json("close", target_id)
        except Exception:
            return


def _search_url(query: str, template: str) -> str:
    return template.format(query=quote_plus(query))


def _extract_search_results_with_browser(client: OpenClawBrowserClient, url: str, limit: int) -> List[SearchResultItem]:
    target_id = client.open_url(url)
    try:
        js = """
() => {
  const seen = new Set();
  const anchors = Array.from(document.querySelectorAll('a[href]'));
  const items = [];
  for (const anchor of anchors) {
    const href = String(anchor.href || '').trim();
    const title = String(anchor.textContent || '').replace(/\\s+/g, ' ').trim();
    if (!href || !title) continue;
    if (!/^https?:\\/\\//i.test(href)) continue;
    if (href.includes('duckduckgo.com') || href.includes('javascript:')) continue;
    if (seen.has(href)) continue;
    seen.add(href);
    const container = anchor.closest('article,.result,.result__body,.web-result') || anchor.parentElement || document.body;
    const snippet = String(container.textContent || '').replace(/\\s+/g, ' ').trim();
    items.push({ title, url: href, snippet });
    if (items.length >= 12) break;
  }
  return items;
}
"""
        result = client.evaluate(target_id, js)
        items: List[SearchResultItem] = []
        if isinstance(result, list):
            for raw in result:
                if not isinstance(raw, dict):
                    continue
                href = _normalize_space(str(raw.get("url", "")))
                title = _normalize_space(str(raw.get("title", "")))
                if not href or not title:
                    continue
                items.append(
                    SearchResultItem(
                        title=title,
                        url=href,
                        snippet=_normalize_space(str(raw.get("snippet", "")))[:400],
                    )
                )
                if len(items) >= limit:
                    return items
        if items:
            return items
        snapshot = client.snapshot(target_id, limit=300)
        return extract_search_results_from_snapshot(snapshot, limit=limit)
    finally:
        client.close(target_id)


def _fetch_page_text(client: OpenClawBrowserClient, url: str) -> FetchedPage:
    target_id = client.open_url(url)
    try:
        result = client.evaluate(
            target_id,
            """
() => ({
  title: document.title || '',
  text: (document.body && document.body.innerText ? document.body.innerText : '').slice(0, 4000)
})
""",
        )
        if not isinstance(result, dict):
            raise RuntimeError("browser evaluate returned invalid page payload")
        return FetchedPage(
            url=url,
            title=_normalize_space(str(result.get("title", ""))),
            text=_normalize_space(str(result.get("text", ""))),
        )
    finally:
        client.close(target_id)


def run_search_enrichment(
    case: RecognitionCase,
    baseline_document: StructuredDocument,
    *,
    client: Optional[OpenClawBrowserClient] = None,
    search_template: str = DEFAULT_SEARCH_TEMPLATE,
    max_results: int = 5,
    max_pages: int = 2,
) -> SearchEvidenceBundle:
    browser = client or OpenClawBrowserClient()
    queries = [build_site_query(case, baseline_document), build_broad_query(case, baseline_document)]
    queries = [query for query in queries if query]
    results: List[SearchResultItem] = []
    warnings: List[str] = []
    for query in queries:
        try:
            found = _extract_search_results_with_browser(browser, _search_url(query, search_template), limit=max_results)
            for item in found:
                if item.url not in {existing.url for existing in results}:
                    results.append(item)
                if len(results) >= max_results:
                    break
        except Exception as exc:
            warnings.append(f"search_query_failed:{query}:{exc}")

    fetched_pages: List[FetchedPage] = []
    for item in results[:max_pages]:
        try:
            fetched_pages.append(_fetch_page_text(browser, item.url))
        except Exception as exc:
            warnings.append(f"search_fetch_failed:{item.url}:{exc}")

    evidence_parts: List[str] = []
    if queries:
        evidence_parts.append("[搜索查询]\n" + "\n".join(queries))
    if results:
        evidence_parts.append(
            "[搜索结果]\n"
            + "\n".join(
                f"- {item.title} | {item.url} | {item.snippet}" for item in results
            )
        )
    if fetched_pages:
        evidence_parts.append(
            "[搜索补充页面]\n"
            + "\n\n".join(
                f"标题: {page.title}\n链接: {page.url}\n内容: {page.text}" for page in fetched_pages
            )
        )

    return SearchEvidenceBundle(
        queries=queries,
        results=results[:max_results],
        fetched_pages=fetched_pages[:max_pages],
        evidence_text="\n\n".join(part for part in evidence_parts if part).strip(),
        warnings=warnings,
    )
