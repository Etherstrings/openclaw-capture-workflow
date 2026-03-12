"""Evidence extraction adapters."""

from __future__ import annotations

import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shlex
import subprocess
import time
from typing import Dict, List, Optional
from urllib import request as urlrequest
from urllib.parse import urlparse

from .config import AppConfig
from .content_profile import build_signal_requirements, infer_content_profile
from .models import EvidenceBundle, IngestRequest


def _run_template(command: str, **kwargs: str) -> str:
    rendered = command.format(**kwargs)
    args = shlex.split(rendered)
    try:
        result = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        tail = stderr or stdout or str(exc)
        if len(tail) > 1400:
            tail = tail[-1400:]
        raise RuntimeError(f"command failed: {' '.join(args)} | {tail}") from exc
    return result.stdout.strip()


def _quote_for_shell(value: str) -> str:
    return shlex.quote(value)


def _strip_html(value: str) -> str:
    value = re.sub(r"(?is)<script.*?>.*?</script>", " ", value)
    value = re.sub(r"(?is)<style.*?>.*?</style>", " ", value)
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _strip_html_preserve_newlines(value: str) -> str:
    value = re.sub(r"(?is)<script.*?>.*?</script>", " ", value)
    value = re.sub(r"(?is)<style.*?>.*?</style>", " ", value)
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = value.replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _normalize_text(value: str) -> str:
    value = html.unescape(value)
    value = value.replace("\r", "\n")
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def _looks_like_url_only(text: str, source_url: str | None = None) -> bool:
    value = text.strip()
    if not value:
        return False
    if source_url and value == source_url.strip():
        return True
    return bool(re.fullmatch(r"https?://\S+", value))


def _looks_like_legal_footer(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    footer_tokens = [
        "沪ICP备",
        "营业执照",
        "增值电信业务经营许可证",
        "医疗器械网络交易服务第三方平台备案",
        "互联网药品信息服务资格证书",
        "违法不良信息举报电话",
        "网络文化经营许可证",
        "个性化推荐算法",
    ]
    hits = sum(1 for token in footer_tokens if token in normalized)
    return hits >= 3


def _normalize_url_for_match(url: str) -> str:
    parsed = urlparse(url)
    query = parsed.query
    if parsed.netloc.endswith("xiaohongshu.com"):
        query = "&".join(
            part
            for part in parsed.query.split("&")
            if part and not part.startswith(("shareRedId=", "apptime=", "share_id=", "author_share="))
        )
    normalized = parsed._replace(fragment="", query=query)
    return normalized.geturl()


def _extract_title(body: str) -> str | None:
    for pattern in [
        r'(?is)<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
        r'(?is)<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\'](.*?)["\']',
        r"(?is)<title[^>]*>(.*?)</title>",
    ]:
        match = re.search(pattern, body)
        if match:
            title = _normalize_text(_strip_html(match.group(1)))
            if title:
                return title
    return None


def _extract_meta_description(body: str) -> str | None:
    for pattern in [
        r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        r'(?is)<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
    ]:
        match = re.search(pattern, body)
        if match:
            description = _normalize_text(match.group(1))
            if description:
                return description
    return None


def _html_to_structured_text(value: str) -> str:
    value = re.sub(r"(?is)<br\\s*/?>", "\n", value)
    value = re.sub(r"(?is)</?(h[1-6])\\b[^>]*>", "\n", value)
    value = re.sub(r"(?is)</?(p|div|section|article|li|ul|ol|blockquote)\\b[^>]*>", "\n", value)
    value = re.sub(r"(?is)</?(pre|code)\\b[^>]*>", "\n", value)
    return _strip_html_preserve_newlines(value)


def _extract_tencent_article(body: str) -> str | None:
    class TencentArticleParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.in_target = False
            self.div_depth = 0
            self.chunks: list[str] = []
            self.in_code = False
            self.in_pre = False

        def handle_starttag(self, tag: str, attrs) -> None:
            if tag in {"script", "style"}:
                return
            attr_map = {k: v for k, v in attrs if v is not None}
            if tag == "div":
                class_attr = attr_map.get("class", "")
                if not self.in_target and "article-content" in class_attr:
                    self.in_target = True
                    self.div_depth = 1
                elif self.in_target:
                    self.div_depth += 1
            if not self.in_target:
                return
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "br", "pre", "code"}:
                self.chunks.append("\n")
            if tag == "pre":
                self.in_pre = True
            if tag == "code":
                self.in_code = True

        def handle_endtag(self, tag: str) -> None:
            if not self.in_target:
                return
            if tag == "div":
                self.div_depth -= 1
                if self.div_depth <= 0:
                    self.in_target = False
                    self.div_depth = 0
                    self.chunks.append("\n")
                    return
            if tag in {"p", "li", "pre", "code"}:
                self.chunks.append("\n")
            if tag == "code":
                self.in_code = False
            if tag == "pre":
                self.in_pre = False

        def handle_data(self, data: str) -> None:
            if not self.in_target:
                return
            text = data.strip()
            if not text:
                return
            if self.in_code and self.in_pre:
                self.chunks.append(f"命令：{text}")
            else:
                self.chunks.append(text)

    parser = TencentArticleParser()
    parser.feed(body)
    raw_text = _strip_html_preserve_newlines(" ".join(parser.chunks))
    if not raw_text:
        return None
    lines: list[str] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _looks_like_ui_noise(line) or _looks_like_step_noise(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<br\\s*/?>", "\n", value)
    value = re.sub(r"(?is)</?(p|div|article|section|h[1-6]|li|ul|ol|blockquote|pre|tr)>", "\n", value)
    return _normalize_text(_strip_html(value))


def _extract_wechat_article(body: str) -> str | None:
    match = re.search(r'(?is)<div[^>]+id=["\']js_content["\'][^>]*>(.*?)</div>', body)
    if not match:
        return None
    text = _html_to_text(match.group(1))
    return text or None


def _extract_article_blocks(body: str) -> str | None:
    candidates: list[tuple[int, str]] = []
    patterns = [
        r'(?is)<article\b[^>]*>(.*?)</article>',
        r'(?is)<main\b[^>]*>(.*?)</main>',
        r'(?is)<div[^>]+class=["\'][^"\']*(?:article|content|post|entry|rich_media_content|note-content)[^"\']*["\'][^>]*>(.*?)</div>',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, body):
            text = _html_to_text(match.group(1))
            if len(text) >= 40:
                candidates.append((len(text), text))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    return None


def _extract_paragraph_fallback(body: str) -> str:
    parts: list[str] = []
    for match in re.finditer(r"(?is)<p\b[^>]*>(.*?)</p>", body):
        text = _html_to_text(match.group(1))
        if len(text) >= 20:
            parts.append(text)
    if not parts:
        return _html_to_text(body)
    return _normalize_text("\n\n".join(parts))


def _fetch_html_document(url: str) -> tuple[str | None, str]:
    req = urlrequest.Request(url, headers={"User-Agent": "Mozilla/5.0 OpenClawCaptureWorkflow/0.1"})
    with urlrequest.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
    title = _extract_title(body)
    if "cloud.tencent.com/developer/article" in url:
        tencent_text = _extract_tencent_article(body)
        if tencent_text:
            return title, tencent_text
    if "xiaohongshu.com" in url:
        description = _extract_meta_description(body)
        if description and not _looks_like_legal_footer(description):
            return title, description
    text = _extract_wechat_article(body) or _extract_article_blocks(body) or _extract_paragraph_fallback(body)
    return title, text


def _github_repo_from_url(url: str | None) -> tuple[str, str] | None:
    if not url:
        return None
    parsed = urlparse(url)
    if "github.com" not in (parsed.netloc or "").lower():
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner = parts[0].strip()
    repo = parts[1].strip().removesuffix(".git")
    if not owner or not repo:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,39}", owner):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo):
        return None
    return owner, repo


def _github_blob_from_url(url: str | None) -> tuple[str, str, str, str] | None:
    if not url:
        return None
    parsed = urlparse(url)
    if "github.com" not in (parsed.netloc or "").lower():
        return None
    parts = [part for part in parsed.path.split("/") if part]
    # /{owner}/{repo}/blob/{branch}/{path...}
    if len(parts) < 5:
        return None
    owner, repo, marker = parts[0], parts[1].removesuffix(".git"), parts[2].lower()
    if marker not in {"blob", "raw"}:
        return None
    branch = parts[3]
    file_path = "/".join(parts[4:])
    if not owner or not repo or not branch or not file_path:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,39}", owner):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo):
        return None
    return owner, repo, branch, file_path


def _fetch_json(url: str, headers: dict[str, str] | None = None) -> dict:
    req = urlrequest.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0 OpenClawCaptureWorkflow/0.1"})
    with urlrequest.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
    return json.loads(raw)


def _fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
    req = urlrequest.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0 OpenClawCaptureWorkflow/0.1"})
    with urlrequest.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _normalize_markdown_line(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = re.sub(r"^>\s*", "", text)
    text = re.sub(r"^\d+\.\s+", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("`", "")
    text = text.strip(" -*\t")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_readme_key_lines(readme: str) -> list[str]:
    if not readme:
        return []
    candidates: list[tuple[int, int, str]] = []
    in_code_block = False
    for idx, raw in enumerate(readme.splitlines()):
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        line = _normalize_markdown_line(raw)
        if not line:
            continue
        lowered = line.lower()
        if line.startswith("[") and line.endswith("]"):
            continue
        if len(line) < 8:
            continue
        if line in {"License", "MIT license", "Contributors", "Resources", "About", "Releases", "Packages"}:
            continue
        if any(token in lowered for token in ["uh oh!", "error while loading", "watchers", "forks", "stars"]):
            continue
        if line.startswith(("http://", "https://")) and ".skill" not in lowered:
            continue

        score = 0
        if "/install-skill" in lowered:
            score += 10
        if ".skill" in lowered:
            score += 9
        if "kubelet" in lowered:
            score += 8
        if "container runtime" in lowered or "container-runtime" in lowered:
            score += 8
        if "cri " in lowered or lowered.startswith("cri") or "cri-" in lowered:
            score += 5
        if "cgroup" in lowered:
            score += 4
        if "installation" in lowered or "install via url" in lowered or "manual download" in lowered:
            score += 8
        if "usage" in lowered or "use cases" in lowered or "basic usage" in lowered or "advanced usage" in lowered:
            score += 6
        if "report structure" in lowered or "evidence standards" in lowered:
            score += 5
        if "skill" in lowered:
            score += 4
        if "github.com/" in lowered:
            score += 4
        if bool(re.search(r"\b[a-z][a-z0-9]+(?:-[a-z0-9]+){1,5}\b", lowered)):
            score += 3
        if any(token in lowered for token in ["disclaimer", "license", "contributor", "copyright"]):
            score -= 4
        if score <= 0:
            continue

        if len(line) > 160:
            line = line[:160].rstrip() + "..."
        candidates.append((score, idx, line))

    if not candidates:
        return []
    ranked = sorted(candidates, key=lambda item: (item[0], -item[1]), reverse=True)
    picked = sorted(ranked[:20], key=lambda item: item[1])
    results: list[str] = []
    for _, _, line in picked:
        if line in results:
            continue
        results.append(line)
        if len(results) >= 14:
            break
    # Keep critical technical terms when present in source markdown even if ranking misses them.
    focus_terms = ("kubelet", "container runtime", "container-runtime", "cri", "cgroup")
    if len(results) < 18:
        for raw in readme.splitlines():
            line = _normalize_markdown_line(raw)
            if not line:
                continue
            lowered = line.lower()
            if not any(term in lowered for term in focus_terms):
                continue
            if line in results:
                continue
            results.append(line[:160].rstrip() + ("..." if len(line) > 160 else ""))
            if len(results) >= 18:
                break
    return results


def _fetch_github_repo_summary(url: str) -> tuple[str | None, str, dict[str, object]]:
    repo_ref = _github_repo_from_url(url)
    if not repo_ref:
        return None, "", {}
    owner, repo = repo_ref
    headers = {
        "User-Agent": "Mozilla/5.0 OpenClawCaptureWorkflow/0.1",
        "Accept": "application/vnd.github+json",
    }
    repo_api = f"https://api.github.com/repos/{owner}/{repo}"
    payload = _fetch_json(repo_api, headers=headers)
    if not isinstance(payload, dict):
        return None, "", {}
    if payload.get("message") == "Not Found":
        return None, "", {}
    full_name = str(payload.get("full_name") or f"{owner}/{repo}").strip()
    html_url = str(payload.get("html_url") or f"https://github.com/{owner}/{repo}").strip()
    description = str(payload.get("description") or "").strip()
    lines: list[str] = [
        f"项目仓库: {full_name}",
        f"仓库地址: {html_url}",
    ]
    if description:
        lines.append(f"仓库简介: {description}")

    readme_lines: list[str] = []
    try:
        readme_raw = _fetch_text(
            f"https://api.github.com/repos/{owner}/{repo}/readme",
            headers={
                "User-Agent": "Mozilla/5.0 OpenClawCaptureWorkflow/0.1",
                "Accept": "application/vnd.github.raw+json",
            },
        )
        readme_lines = _extract_readme_key_lines(readme_raw)
    except Exception:
        readme_lines = []
    if readme_lines:
        lines.append("[README关键信息]")
        lines.extend(readme_lines[:14])

    metadata: dict[str, object] = {
        "source": "github_api",
        "repo": full_name,
        "html_url": html_url,
    }
    title = full_name if full_name else f"{owner}/{repo}"
    return title, "\n".join(lines).strip(), metadata


def _fetch_github_blob_summary(url: str) -> tuple[str | None, str, dict[str, object]]:
    blob_ref = _github_blob_from_url(url)
    if not blob_ref:
        return None, "", {}
    owner, repo, branch, file_path = blob_ref
    lowered_path = file_path.lower()
    if not lowered_path.endswith((".md", ".markdown", ".mdx", ".txt", ".rst")):
        return None, "", {}
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{file_path}"
    raw_text = _fetch_text(raw_url, headers={"User-Agent": "Mozilla/5.0 OpenClawCaptureWorkflow/0.1"})
    lines = _extract_readme_key_lines(raw_text)
    title = Path(file_path).name
    repo_full = f"{owner}/{repo}"
    out_lines: list[str] = [
        f"项目仓库: {repo_full}",
        f"文档路径: {file_path}",
        f"文档链接: {url}",
        f"Raw链接: {raw_url}",
    ]
    if lines:
        out_lines.append("[文档关键信息]")
        out_lines.extend(lines[:16])
    metadata: dict[str, object] = {
        "source": "github_blob",
        "repo": repo_full,
        "blob_path": file_path,
        "raw_url": raw_url,
        "html_url": f"https://github.com/{repo_full}",
    }
    return title, "\n".join(out_lines).strip(), metadata


def _tighten_github_signals(signals: Dict[str, list[str]], source_url: str | None) -> Dict[str, list[str]]:
    if not signals:
        return {}
    filtered: Dict[str, list[str]] = {}
    repo_ref = _github_repo_from_url(source_url)
    canonical_project = None
    canonical_repo_url = None
    if repo_ref:
        canonical_project = f"{repo_ref[0]}/{repo_ref[1]}"
        canonical_repo_url = f"https://github.com/{canonical_project}"

    if canonical_project:
        filtered["projects"] = [canonical_project]
    elif signals.get("projects"):
        filtered["projects"] = _dedupe_strings(signals.get("projects", []), limit=2)

    links = [str(item).strip() for item in signals.get("links", []) if str(item).strip()]
    if canonical_repo_url:
        ordered_links: list[str] = [canonical_repo_url]
        if source_url and source_url.lower().startswith("https://github.com/"):
            ordered_links.append(source_url)
        for link in links:
            lowered = link.lower()
            if not lowered.startswith("https://github.com/"):
                continue
            if link == canonical_repo_url:
                continue
            if source_url and link == source_url:
                continue
            if "/raw/" in lowered or lowered.endswith(".skill"):
                ordered_links.append(link)
        filtered["links"] = _dedupe_strings(ordered_links, limit=3)
    elif links:
        filtered["links"] = _dedupe_strings(links, limit=3)

    for key, limit in [("skills", 3), ("skill_ids", 4), ("commands", 2), ("hashtags", 6)]:
        values = [str(item).strip() for item in signals.get(key, []) if str(item).strip()]
        if values:
            filtered[key] = _dedupe_strings(values, limit=limit)
    return {key: values for key, values in filtered.items() if values}


def _run_openclaw_browser_json(*args: str) -> dict:
    result = subprocess.run(
        ["openclaw", "browser", *args, "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _find_browser_tab_for_url(url: str, tabs: list[dict]) -> dict | None:
    normalized = _normalize_url_for_match(url)
    for tab in tabs:
        tab_url = tab.get("url", "")
        if tab_url == url or _normalize_url_for_match(tab_url) == normalized:
            return tab
    for tab in tabs:
        tab_url = tab.get("url", "")
        if parsed_url_path_key(tab_url) == parsed_url_path_key(url):
            return tab
    return None


def parsed_url_path_key(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return parsed.netloc, path


def _find_or_open_browser_tab(url: str, retries: int = 8, delay_seconds: float = 0.6) -> dict | None:
    tabs_payload = _run_openclaw_browser_json("tabs")
    tabs = tabs_payload.get("tabs", [])
    tab = _find_browser_tab_for_url(url, tabs)
    if tab:
        return tab
    _run_openclaw_browser_json("open", url)
    for _ in range(retries):
        time.sleep(delay_seconds)
        tabs_payload = _run_openclaw_browser_json("tabs")
        tabs = tabs_payload.get("tabs", [])
        tab = _find_browser_tab_for_url(url, tabs)
        if tab:
            return tab
    return None


def _looks_like_ui_noise(text: str) -> bool:
    cleaned = text.strip().strip('"')
    if not cleaned:
        return True
    noise_tokens = {
        "发现",
        "直播",
        "发布",
        "通知",
        "我",
        "关注",
        "更多",
        "活动",
        "发送",
        "取消",
        "创作中心",
        "业务合作",
        "搜索小红书",
        "说点什么...",
        "共 19 条评论",
        "举报",
        "文章被收录于专栏：",
        "目录",
        "推荐阅读",
        "作者相关精选",
        "相关产品与服务",
        "热门产品",
        "社区",
        "活动",
        "圈层",
        "关于",
        "免责声明",
        "友情链接",
        "产品介绍",
        "产品文档",
        "加载更多",
        "交个朋友",
    }
    if cleaned in noise_tokens:
        return True
    if cleaned.startswith("#"):
        return True
    if _looks_like_legal_footer(cleaned):
        return True
    if re.search(r"^\d+/\d+$", cleaned):
        return True
    if re.search(r"^\"?\d+\"?$", cleaned):
        return True
    if re.search(r"^\d+天前", cleaned):
        return True
    if cleaned.endswith("作者") or cleaned == "作者":
        return True
    if cleaned.startswith("地址：") and re.search(r"[省市区路号弄座室]", cleaned):
        return True
    if cleaned.startswith("电话：") and re.search(r"[0-9\-]{6,}", cleaned):
        return True
    if any(token in cleaned for token in ["行吟信息科技", "版权所有", "备案号", "网络文化经营许可证", "违法和不良信息举报"]):
        return True
    return False


def _looks_like_step_noise(text: str) -> bool:
    for token in [
        "代码语言",
        "AI代码解释",
        "为所有代码块添加",
        "定位问题代码",
        "目录",
        "推荐阅读",
        "注意：",
        "安全提示",
        "举报",
    ]:
        if token in text:
            return True
    return False


def _looks_like_comment_noise(text: str) -> bool:
    candidate = text.strip()
    if not candidate:
        return False
    if re.search(r"[？?]$", candidate) and len(candidate) <= 36:
        return True
    if any(candidate.startswith(prefix) for prefix in ["你这个", "我是", "我想问", "请问", "有人知道"]):
        return True
    if any(token in candidate for token in ["评论", "回复", "共 ", "条评论"]):
        return True
    return False


def _looks_like_command_line(text: str) -> bool:
    value = text.strip()
    if not value:
        return False
    if value.startswith("http://") or value.startswith("https://"):
        return False
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", value))
    if has_cjk:
        return False
    command_tokens = [
        "set-executionpolicy",
        "iwr ",
        "openclaw",
        "npm ",
        "node ",
        "brew ",
        "pip ",
        "python ",
        "curl ",
        "wget ",
        "git ",
    ]
    lowered = value.lower()
    command_prefixes = (
        "openclaw ",
        "set-executionpolicy",
        "iwr ",
        "curl ",
        "wget ",
        "npm ",
        "node ",
        "pip ",
        "python ",
        "git ",
        "brew ",
        "bash ",
        "sh ",
    )
    # Filter long prose-like English lines that accidentally include tokens such as "openclaw".
    word_count = len(value.split())
    if (
        word_count > 7
        and not has_cjk
        and "|" in value
        and not lowered.startswith(command_prefixes)
        and not any(symbol in value for symbol in ["--", " /", "\\", "=", ";"])
    ):
        return False
    if any(token in lowered for token in command_tokens):
        if "openclaw" in lowered and " " not in value and "-" not in value and "_" not in value and "/" not in value:
            return False
        if has_cjk and not lowered.startswith(("openclaw ", "set-executionpolicy", "iwr ", "curl ", "wget ", "npm ", "node ", "pip ", "python ", "git ")):
            return False
        return True
    if "|" in value and any(token.strip() in lowered for token in ["openclaw", "iwr", "curl", "wget", "npm", "python"]):
        return not has_cjk
    return False


def _normalize_command_line(text: str) -> str:
    value = text.strip()
    if not value:
        return value
    match = re.search(r"[\u4e00-\u9fff]", value)
    if match:
        value = value[: match.start()].strip()
    value = value.rstrip("；;：:，。")
    return value


def _is_high_value_link(url: str, source_url: str | None = None) -> bool:
    lowered = url.lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    low_value_domains = [
        "creator.xiaohongshu.com",
        "beian.cac.gov.cn",
        "icp.chinaz.com",
    ]
    if any(domain in lowered for domain in low_value_domains):
        return False
    if source_url and "xiaohongshu.com" in source_url and "xiaohongshu.com" in lowered:
        return False
    high_value_domains = [
        "github.com",
        "gitee.com",
        "gitlab.com",
        "npmjs.com",
        "pypi.org",
        "huggingface.co",
        "openclaw.ai",
        "docs.",
    ]
    return any(domain in lowered for domain in high_value_domains)


def _clean_skill_name(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    text = text.strip("，。；;：:、- ")
    text = text.replace("「", "").replace("」", "")
    if len(text) > 40:
        text = text[:40].rstrip()
    return text


def _is_noisy_skill_name(value: str) -> bool:
    text = re.sub(r"\s+", " ", (value or "").strip())
    lowered = text.lower()
    if not text:
        return True
    if lowered in {"skill", "openclaw skill"}:
        return True
    if lowered.startswith(("install-", "install ", "github", "http", "day1global-")):
        return True
    if re.fullmatch(r"[a-z0-9._-]+\s*skill", lowered):
        return True
    if "install" in lowered and not any(token in lowered for token in ["analysis", "analy", "分析"]):
        return True
    return False


def _repo_token_has_letter(value: str) -> bool:
    return bool(re.search(r"[A-Za-z]", value))


def _looks_like_time_fragment(value: str) -> bool:
    token = value.strip().lower()
    if not token:
        return False
    if re.fullmatch(r"\d{1,2}", token):
        return True
    if re.fullmatch(r"[0-5]?\d(?:s|m|min|sec)?", token):
        return True
    return False


def _is_valid_repo_candidate(owner: str, repo: str) -> bool:
    owner_value = owner.strip(".-_/")
    repo_value = repo.strip(".-_/")
    if not owner_value or not repo_value:
        return False
    lowered_owner = owner_value.lower()
    if lowered_owner in {"github", "github.com", "www.github.com"}:
        return False
    if lowered_owner.startswith("http"):
        return False
    if "." in lowered_owner and lowered_owner.endswith((".com", ".cn", ".org", ".net", ".io")):
        return False
    # Avoid timestamp/page-counter noise such as "00:00 / 01:43" -> "00/01".
    if not _repo_token_has_letter(owner_value) or not _repo_token_has_letter(repo_value):
        return False
    if _looks_like_time_fragment(owner_value) or _looks_like_time_fragment(repo_value):
        return False
    disallowed_tokens = {"issues", "pull", "pulls", "actions", "wiki", "releases", "blob", "tree"}
    if owner_value.lower() in disallowed_tokens or repo_value.lower() in disallowed_tokens:
        return False
    return True


def _extract_text_from_browser_snapshot(snapshot: str) -> str:
    candidates: list[str] = []
    urls: list[str] = []
    for raw_line in snapshot.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "/url:" in line:
            url_match = re.search(r"/url:\s*(https?://\S+)", line)
            if url_match:
                url = url_match.group(1).rstrip(")]}>,")
                if _is_high_value_link(url) and url not in urls:
                    urls.append(url)
            continue
        text_match = re.search(r"- text:\s*(.+)$", line)
        if text_match:
            text = _normalize_text(text_match.group(1))
            if text and not _looks_like_ui_noise(text) and not _looks_like_comment_noise(text):
                candidates.append(text)
            continue
        node_match = re.search(r"- (?:generic|paragraph|heading|article)\b.*?:\s*(.+)$", line)
        if node_match:
            text = _normalize_text(node_match.group(1))
            if len(text) >= 8 and not _looks_like_ui_noise(text) and not _looks_like_comment_noise(text):
                candidates.append(text)
    deduped: list[str] = []
    seen: set[str] = set()
    for text in candidates:
        if text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    if not deduped:
        return "\n".join(urls[:6]).strip() if urls else ""
    body_candidates = [text for text in deduped if len(text) >= 12]
    if body_candidates:
        body = "\n\n".join(body_candidates[:6]).strip()
    else:
        body = "\n\n".join(deduped[:8]).strip()
    if urls:
        body = body.rstrip() + "\n\n相关链接：\n" + "\n".join(urls[:6])
    return body.strip()


def _extract_skill_signals(text: str, source_url: str | None = None) -> Dict[str, list[str]]:
    signals: Dict[str, list[str]] = {
        "skills": [],
        "projects": [],
        "skill_ids": [],
        "commands": [],
        "links": [],
        "hashtags": [],
        "prerequisites": [],
        "validation_actions": [],
        "use_cases": [],
        "purposes": [],
        "boundaries": [],
        "common_errors": [],
    }
    normalized = text or ""
    url_pattern = r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+"
    for match in re.findall(url_pattern, normalized):
        url = match.rstrip(")]}>,.;，。；：")
        if url and _is_high_value_link(url, source_url) and url not in signals["links"]:
            signals["links"].append(url)

    # Accept bare repo URLs such as github.com/owner/repo (without protocol).
    for owner, repo in re.findall(
        r"(?i)(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]{1,39})/([A-Za-z0-9_.-]{1,100})",
        normalized,
    ):
        owner = owner.strip(".-_/")
        repo = repo.strip(".-_/")
        if not _is_valid_repo_candidate(owner, repo):
            continue
        repo_key = f"{owner}/{repo}"
        if repo_key not in signals["projects"]:
            signals["projects"].append(repo_key)
        normalized_url = f"https://github.com/{owner}/{repo}"
        if normalized_url not in signals["links"]:
            signals["links"].append(normalized_url)

    # Infer repo from OCR lines only for non-GitHub source pages.
    if not (source_url and "github.com/" in source_url.lower()):
        for line in [item.strip() for item in normalized.splitlines() if item.strip()]:
            owner_repo_match = re.search(
                r"\b([A-Za-z0-9_.-]{1,39})\s*/\s*([A-Za-z0-9_.-]{2,100})\b",
                line,
            )
            if not owner_repo_match:
                continue
            owner = owner_repo_match.group(1).strip(".-_/")
            repo = owner_repo_match.group(2).strip(".-_/")
            if not _is_valid_repo_candidate(owner, repo):
                continue
            lowered = line.lower()
            line_has_repo_context = (
                "github.com" in lowered
                or "public" in lowered
                or "private" in lowered
                or "repository" in lowered
                or "repo" in lowered
                or "仓库" in line
            )
            if not line_has_repo_context:
                continue
            repo_key = f"{owner}/{repo}"
            if repo_key not in signals["projects"]:
                signals["projects"].append(repo_key)
            normalized_url = f"https://github.com/{owner}/{repo}"
            if normalized_url not in signals["links"]:
                signals["links"].append(normalized_url)
    if source_url:
        source_lower = source_url.lower()
        force_source_link = any(
            token in source_lower
            for token in [
                "bilibili.com/video/",
                "youtube.com/watch",
                "youtu.be/",
                "vimeo.com/",
                "xiaohongshu.com/explore/",
            ]
        )
        if (force_source_link or _is_high_value_link(source_url, source_url)) and source_url not in signals["links"]:
            signals["links"].append(source_url)

    for match in re.findall(r"「([^」]{2,60})」\s*Skill", normalized, re.IGNORECASE):
        skill = _clean_skill_name(f"{match} Skill")
        if _is_noisy_skill_name(skill):
            continue
        if skill and skill not in signals["skills"]:
            signals["skills"].append(skill)
    for match in re.findall(r"「([^」]{2,80}Skill)」", normalized, re.IGNORECASE):
        skill = _clean_skill_name(match)
        if _is_noisy_skill_name(skill):
            continue
        if skill and skill not in signals["skills"]:
            signals["skills"].append(skill)
    for match in re.findall(r"([A-Za-z0-9\u4e00-\u9fff\-]{2,40})\s*Skill", normalized, re.IGNORECASE):
        skill = _clean_skill_name(f"{match} Skill")
        if any(token in skill for token in ["http", "/", ".", ":"]):
            continue
        if any(token in skill for token in ["最近在", "看到有人", "分享", "试用", "安装方法"]):
            continue
        if _is_noisy_skill_name(skill):
            continue
        if skill and skill not in signals["skills"]:
            signals["skills"].append(skill)
        if len(signals["skills"]) >= 6:
            break

    for line in [line.strip() for line in normalized.splitlines() if line.strip()]:
        if _looks_like_command_line(line):
            cmd = _normalize_command_line(line)
            if cmd and cmd not in signals["commands"]:
                signals["commands"].append(cmd)
        if line.startswith("命令："):
            cmd = _normalize_command_line(line.replace("命令：", "", 1).strip())
            if cmd and cmd not in signals["commands"]:
                signals["commands"].append(cmd)
        if len(signals["commands"]) >= 6:
            break
    slug_stoplist = {
        "install-skill",
        "openclaw-capture-workflow",
        "institutional-grade",
        "long-term",
        "multi-method",
        "probability-weighted",
        "anti-bias",
        "position-sizing",
        "deep-analysis",
    }
    for line in [item.strip() for item in normalized.splitlines() if item.strip()]:
        lowered_line = line.lower()
        has_skill_context = (
            "skill id" in lowered_line
            or "技能id" in lowered_line
            or "技能id" in line.lower()
            or ".skill" in lowered_line
            or "/install-skill" in lowered_line
            or line.startswith(("技能ID:", "Skill ID:", "skill id:"))
        )
        for match in re.findall(r"\b[a-z][a-z0-9]+(?:-[a-z0-9]+){1,5}\b", line):
            slug = match.strip()
            if slug.startswith(("http", "https")):
                continue
            if any(token in slug for token in [".com", ".cn", ".org"]):
                continue
            if slug in slug_stoplist:
                continue
            standalone = line.strip().lower() == slug
            if not (has_skill_context or standalone):
                continue
            if slug not in signals["skill_ids"]:
                signals["skill_ids"].append(slug)
            if len(signals["skill_ids"]) >= 6:
                break
        if len(signals["skill_ids"]) >= 6:
            break

    for tag in re.findall(r"#([A-Za-z0-9_\-\u4e00-\u9fff]{2,30})", normalized):
        hashtag = f"#{tag}"
        if hashtag not in signals["hashtags"]:
            signals["hashtags"].append(hashtag)
        if len(signals["hashtags"]) >= 10:
            break

    for line in [item.strip() for item in normalized.splitlines() if item.strip()]:
        if len(line) < 6 or len(line) > 220:
            continue
        lowered = line.lower()
        compact = lowered.replace(" ", "")
        if any(token in lowered for token in ["前置", "依赖", "需要安装", "requirement", "prerequisite"]) or line.startswith(
            ("前置条件", "环境要求", "准备工作")
        ):
            if line not in signals["prerequisites"]:
                signals["prerequisites"].append(line)
        if any(token in lowered for token in ["验证", "检查", "确认", "访问", "打开", "运行后", "完成后", "check", "verify"]):
            if line not in signals["validation_actions"]:
                signals["validation_actions"].append(line)
        if any(token in lowered for token in ["可直接问", "可以直接问", "适合", "用于", "支持", "use it", "ask about"]):
            if line not in signals["use_cases"]:
                signals["use_cases"].append(line)
        if any(token in lowered for token in ["用于", "帮助", "介绍", "提供", "支持", "专注于", "能够"]) and not _looks_like_command_line(line):
            if line not in signals["purposes"]:
                signals["purposes"].append(line)
        if any(token in compact for token in ["不支持", "仅支持", "限制", "注意事项", "局限"]) and line not in signals["boundaries"]:
            signals["boundaries"].append(line)
        if any(token in compact for token in ["报错", "错误", "失败", "403", "quota", "insufficient", "forbidden"]) and line not in signals["common_errors"]:
            signals["common_errors"].append(line)

    for key in ["prerequisites", "validation_actions", "use_cases", "purposes", "boundaries", "common_errors"]:
        signals[key] = _dedupe_strings(signals[key], limit=6)

    return {key: values for key, values in signals.items() if values}


def _add_evidence_source(metadata: Dict[str, object], source_name: str) -> None:
    sources = metadata.get("evidence_sources", [])
    if not isinstance(sources, list):
        sources = []
    if source_name not in sources:
        sources.append(source_name)
    metadata["evidence_sources"] = sources


def _finalize_evidence_metadata(
    metadata: Dict[str, object],
    *,
    source_kind: str,
    source_url: str | None,
    text: str,
) -> Dict[str, object]:
    signals = metadata.get("signals", {}) if isinstance(metadata.get("signals"), dict) else {}
    profile = infer_content_profile(source_kind, source_url, text, metadata)
    metadata["content_profile"] = profile
    metadata["signal_requirements"] = build_signal_requirements(profile, signals)
    if isinstance(metadata.get("evidence_sources"), list):
        metadata["evidence_sources"] = _dedupe_strings([str(item) for item in metadata["evidence_sources"]], limit=20)
    return metadata


def _append_signal_hint(text: str, signals: Dict[str, list[str]]) -> str:
    if not signals:
        return text
    lines: list[str] = []
    if signals.get("skills"):
        lines.append("技能名: " + " | ".join(signals["skills"][:4]))
    if signals.get("skill_ids"):
        lines.append("技能ID: " + " | ".join(signals["skill_ids"][:4]))
    if signals.get("commands"):
        lines.append("命令: " + " | ".join(signals["commands"][:4]))
    if signals.get("links"):
        lines.append("链接: " + " | ".join(signals["links"][:4]))
    if signals.get("projects"):
        lines.append("项目: " + " | ".join(signals["projects"][:4]))
    if signals.get("hashtags"):
        lines.append("标签: " + " ".join(signals["hashtags"][:6]))
    if not lines:
        return text
    hint_block = "[提取到的关键信号]\n" + "\n".join(lines)
    if not text.strip():
        return hint_block
    return text.rstrip() + "\n\n" + hint_block


def _extract_text_from_tencent_snapshot(snapshot: str) -> str:
    start_markers = [
        "【保姆级教程】手把手教你安装OpenClaw并接入飞书",
        "手把手教你安装OpenClaw并接入飞书",
        "一、什么是 OpenClaw？",
    ]
    stop_markers = [
        "热门产品",
        "社区",
        "活动",
        "圈层",
        "关于",
        "免责声明",
        "友情链接",
        "产品介绍",
        "产品文档",
        "推荐阅读",
        "相关产品与服务",
        "作者相关精选",
        "加载更多",
        "交个朋友",
    ]
    capturing = False
    lines: list[str] = []
    max_lines = 220
    for raw_line in snapshot.splitlines():
        line = raw_line.strip()
        if not line or "/url:" in line:
            continue
        if any(token in line for token in ["Q1:", "Q2:", "Q3:", "Q4:", "Q5:", "加入架构与运维", "即时通信 IM"]):
            break
        if any(marker in line for marker in stop_markers) and sum(len(item) for item in lines) > 8000:
            break
        heading_match = re.search(r'heading "([^"]+)"', line)
        if heading_match:
            heading_text = _normalize_text(heading_match.group(1))
            if any(marker in heading_text for marker in stop_markers) and sum(len(item) for item in lines) > 8000:
                break
            if any(marker in heading_text for marker in start_markers):
                capturing = True
            if capturing and not _looks_like_ui_noise(heading_text):
                lines.append(heading_text)
                if len(lines) >= max_lines:
                    break
            continue
        text_match = re.search(r"- text:\s*(.+)$", line)
        if text_match and capturing:
            text = _normalize_text(text_match.group(1))
            if text and not _looks_like_ui_noise(text) and not text.startswith("发布于"):
                lines.append(text)
                if len(lines) >= max_lines:
                    break
            continue
        code_match = re.search(r"- code\b.*?:\s*(.+)$", line)
        if code_match and capturing:
            code_text = _normalize_text(code_match.group(1))
            if code_text:
                lines.append(f"命令：{code_text}")
                if len(lines) >= max_lines:
                    break
            continue
        node_match = re.search(r"- (?:paragraph|listitem|blockquote|generic)\b.*?:\s*(.+)$", line)
        if node_match and capturing:
            text = _normalize_text(node_match.group(1))
            if text and not _looks_like_ui_noise(text):
                lines.append(text)
                if len(lines) >= max_lines:
                    break
            continue
    deduped: list[str] = []
    seen: set[str] = set()
    for text in lines:
        if text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return "\n".join(deduped).strip()


def _extract_step_items_from_text(text: str) -> list[dict]:
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    steps: list[dict] = []
    current_heading: str | None = None
    details: list[str] = []
    commands: list[str] = []

    def push() -> None:
        nonlocal current_heading, details, commands
        if not current_heading:
            return
        detail = "；".join(details[:3]) if details else None
        command = " | ".join(commands[:2]) if commands else None
        steps.append({"title": current_heading, "detail": detail, "command": command})
        current_heading = None
        details = []
        commands = []

    def is_heading(value: str) -> bool:
        return bool(re.match(r"^[一二三四五六七八九十]+、", value) or re.match(r"^[一二三四五六七八九十]+）", value))

    def is_command(value: str) -> bool:
        candidate = value.strip()
        if len(candidate) < 6:
            return False
        return _looks_like_command_line(candidate)

    for line in lines:
        if is_heading(line):
            push()
            current_heading = line
            continue
        if not current_heading:
            continue
        if line.startswith("命令："):
            cmd = _normalize_command_line(line.replace("命令：", "", 1).strip())
            if cmd and _looks_like_command_line(cmd):
                commands.append(cmd)
            continue
        if is_command(line):
            cmd = _normalize_command_line(line)
            if cmd:
                commands.append(cmd)
            continue
        if len(line) < 6 or len(line) > 140:
            continue
        if _looks_like_ui_noise(line) or _looks_like_step_noise(line):
            continue
        if line.endswith("：") or line.endswith(":"):
            continue
        if line in details:
            continue
        details.append(line)
    push()
    return steps[:30]


def _extract_steps_from_tencent_snapshot(snapshot: str) -> list[dict]:
    steps: list[dict] = []
    current_heading: str | None = None
    current_details: list[str] = []
    current_commands: list[str] = []
    capturing = False

    def clean_detail(value: str) -> str | None:
        candidate = value.strip().strip("；;：: ")
        if len(candidate) < 6:
            return None
        if candidate in {"：", ":", "；", ";"}:
            return None
        if candidate.startswith(("：", ":")):
            return None
        candidate = re.sub(r"(执行：|执行:)$", "", candidate).rstrip()
        candidate = re.sub(r"按$", "", candidate).rstrip()
        candidate = re.sub(r"[，、。；;：:]+$", "", candidate).rstrip()
        candidate = candidate.rstrip("（(").rstrip()
        if len(candidate) > 120:
            candidate = candidate[:120].rstrip()
        candidate = candidate.strip().strip("；;：: ")
        if len(candidate) < 6:
            return None
        if _looks_like_step_noise(candidate):
            return None
        return candidate
    for raw_line in snapshot.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading_match = re.search(r'heading "([^"]+)"', line)
        if heading_match:
            heading_text = _normalize_text(heading_match.group(1))
            if any(marker in heading_text for marker in ["【保姆级教程】手把手教你安装OpenClaw", "手把手教你安装OpenClaw"]):
                capturing = True
                continue
            if not capturing:
                continue
            if re.match(r"^[一二三四五六七八九十]+、", heading_text) or re.match(r"^[一二三四五六七八九十]+）", heading_text):
                if current_heading:
                    steps.append(
                        {
                            "title": current_heading,
                            "detail": "；".join(current_details[:3]) if current_details else None,
                            "command": " | ".join(current_commands[:2]) if current_commands else None,
                        }
                    )
                current_heading = heading_text
                current_details = []
                current_commands = []
            continue
        if not capturing or not current_heading:
            continue
        if "热门产品" in line or "推荐阅读" in line or "相关产品与服务" in line:
            break
        text_match = re.search(r"- text:\s*(.+)$", line)
        if text_match:
            text = _normalize_text(text_match.group(1))
            cleaned = clean_detail(text) if text else None
            if cleaned and not _looks_like_ui_noise(cleaned) and cleaned not in current_details:
                current_details.append(cleaned)
            continue
        code_match = re.search(r"- code\b.*?:\s*(.+)$", line)
        if code_match:
            code_text = _normalize_text(code_match.group(1))
            if code_text and _looks_like_command_line(code_text):
                normalized = _normalize_command_line(code_text)
                if normalized and normalized not in current_commands:
                    current_commands.append(normalized)
            continue
        node_match = re.search(r"- (?:paragraph|listitem|blockquote|generic)\b.*?:\s*(.+)$", line)
        if node_match:
            text = _normalize_text(node_match.group(1))
            cleaned = clean_detail(text) if text else None
            if cleaned and not _looks_like_ui_noise(cleaned) and cleaned not in current_details:
                current_details.append(cleaned)
    if current_heading:
        steps.append(
            {
                "title": current_heading,
                "detail": "；".join(current_details[:3]) if current_details else None,
                "command": " | ".join(current_commands[:2]) if current_commands else None,
            }
        )
    return steps[:30]


def _fetch_openclaw_browser_snapshot(url: str, limit: int = 500) -> tuple[str | None, str]:
    tab = _find_or_open_browser_tab(url)
    if not tab:
        raise RuntimeError("browser tab not found for url")
    snapshot_payload = _run_openclaw_browser_json(
        "snapshot",
        "--target-id",
        tab["targetId"],
        "--limit",
        str(limit),
    )
    title = tab.get("title")
    raw_snapshot = snapshot_payload.get("snapshot", "")
    if "cloud.tencent.com" in url:
        text = _extract_text_from_tencent_snapshot(raw_snapshot)
    else:
        text = _extract_text_from_browser_snapshot(raw_snapshot)
    return title, text


def _fetch_openclaw_browser_snapshot_with_steps(url: str, limit: int = 500) -> tuple[str | None, str, list[dict]]:
    tab = _find_or_open_browser_tab(url)
    if not tab:
        raise RuntimeError("browser tab not found for url")
    snapshot_payload = _run_openclaw_browser_json(
        "snapshot",
        "--target-id",
        tab["targetId"],
        "--limit",
        str(limit),
    )
    raw_snapshot = snapshot_payload.get("snapshot", "")
    title = tab.get("title")
    text = _extract_text_from_tencent_snapshot(raw_snapshot)
    steps = _extract_steps_from_tencent_snapshot(raw_snapshot)
    return title, text, steps


def _resolve_default_ocr_command() -> str:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "ocr_image.swift"
    if script_path.exists():
        return f"swift {_quote_for_shell(str(script_path))} {{input_path}}"
    return ""


def _ocr_command(config: AppConfig) -> str:
    if config.extractors.image_ocr_command.strip():
        return config.extractors.image_ocr_command
    return _resolve_default_ocr_command()


def _dedupe_strings(values: list[str], limit: int | None = None) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
        if limit is not None and len(deduped) >= limit:
            break
    return deduped


def _merge_text_blocks(*blocks: str) -> str:
    cleaned: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        text = (block or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return "\n\n".join(cleaned).strip()


def _score_video_signal_line(line: str) -> int:
    lowered = line.lower()
    score = 0
    if re.match(r"^\[[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?\]", line):
        score += 4
    if any(
        token in lowered
        for token in [
            "github.com/",
            "http://",
            "https://",
            "openclaw",
            "skill",
            "/install-skill",
            "安装",
            "部署",
            "教程",
            "步骤",
            "命令",
            "架构",
            "总结",
            "结论",
            "实践",
            "实战",
        ]
    ):
        score += 4
    if any(token in lowered for token in ["github.com/", "http://", "https://"]):
        score += 4
    if "/install-skill" in lowered or "命令" in line:
        score += 3
    if len(line) >= 14:
        score += 1
    if len(line) > 220:
        score -= 2
    if any(token in line for token in ["点赞", "投币", "收藏", "弹幕", "评论区", "转发"]):
        score -= 5
    if _looks_like_ui_noise(line) or _looks_like_comment_noise(line):
        score -= 6
    if re.fullmatch(r"[0-9: /.-]+", line):
        score -= 4
    return score


def _compact_video_evidence_text(text: str, *, max_lines: int, max_chars: int) -> tuple[str, dict[str, int]]:
    lines_raw = [line for line in (text or "").splitlines() if line.strip()]
    if not lines_raw:
        return "", {"raw_lines": 0, "kept_lines": 0, "raw_chars": 0, "kept_chars": 0}

    entries: list[tuple[int, str, int]] = []
    seen_norm: set[str] = set()
    for idx, raw in enumerate(lines_raw):
        line = _normalize_text(raw)
        if not line:
            continue
        norm = re.sub(r"\s+", " ", line).strip().lower()
        if not norm or norm in seen_norm:
            continue
        seen_norm.add(norm)
        if _looks_like_ui_noise(line) or _looks_like_comment_noise(line):
            continue
        if re.fullmatch(r"\d{1,2}:\d{2}(?:\s*/\s*\d{1,2}:\d{2})?", line):
            continue
        score = _score_video_signal_line(line)
        entries.append((idx, line, score))

    if not entries:
        compact = _normalize_text(text)
        compact = compact[:max_chars].rstrip() if len(compact) > max_chars else compact
        return compact, {
            "raw_lines": len(lines_raw),
            "kept_lines": len(compact.splitlines()) if compact else 0,
            "raw_chars": len((text or "").strip()),
            "kept_chars": len(compact),
        }

    # Keep early context lines + high-signal lines; preserve chronological order.
    seed_count = min(36, max_lines)
    keep_indices: set[int] = set()
    for i, (idx, _, _) in enumerate(entries):
        if i < seed_count:
            keep_indices.add(idx)
    ranked = sorted(entries, key=lambda item: (item[2], -item[0]), reverse=True)
    for idx, _, score in ranked:
        if len(keep_indices) >= max_lines:
            break
        if score >= 6:
            keep_indices.add(idx)
    for idx, _, score in ranked:
        if len(keep_indices) >= max_lines:
            break
        if score >= 2:
            keep_indices.add(idx)
    if len(keep_indices) < min(max_lines, 80):
        for idx, _, _ in entries:
            keep_indices.add(idx)
            if len(keep_indices) >= min(max_lines, 80):
                break

    essential = [(idx, line, score) for idx, line, score in entries if idx in keep_indices and score >= 10]
    essential.sort(key=lambda item: (-item[2], item[0]))
    pinned = [(idx, line) for idx, line, score in entries if idx in keep_indices and 6 <= score < 10]
    pinned.sort(key=lambda item: item[0])
    others = [(idx, line) for idx, line, score in entries if idx in keep_indices and score < 6]
    others.sort(key=lambda item: item[0])
    chosen: list[tuple[int, str]] = []
    seen_idx: set[int] = set()
    for idx, line, _ in essential:
        if idx in seen_idx:
            continue
        chosen.append((idx, line))
        seen_idx.add(idx)
    for idx, line in pinned + others:
        if idx in seen_idx:
            continue
        chosen.append((idx, line))
        seen_idx.add(idx)

    compact_lines: list[str] = []
    char_budget = max(800, int(max_chars))
    used = 0
    for _, line in chosen:
        piece = line
        if len(piece) > 240:
            piece = piece[:240].rstrip() + "..."
        projected = used + len(piece) + (1 if compact_lines else 0)
        if projected > char_budget:
            break
        compact_lines.append(piece)
        used = projected

    compact_text = "\n".join(compact_lines).strip()
    return compact_text, {
        "raw_lines": len(lines_raw),
        "kept_lines": len(compact_lines),
        "raw_chars": len((text or "").strip()),
        "kept_chars": len(compact_text),
    }


def _extract_ocr_lines(text: str, *, strict: bool, limit: int) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = _normalize_text(raw)
        if not line:
            continue
        line = re.sub(r"^[^\w\u4e00-\u9fff#@:/.-]+", "", line).strip()
        if not line:
            continue
        if _looks_like_ui_noise(line) or _looks_like_step_noise(line) or _looks_like_comment_noise(line):
            continue
        lowered = line.lower()
        if re.fullmatch(r"\d{1,2}:\d{2}", line):
            continue
        if re.fullmatch(r"\d+\s*(?:hours?|days?)\s+ago", lowered):
            continue
        if strict:
            interesting = (
                "skill" in lowered
                or "github" in lowered
                or "openclaw" in lowered
                or line.startswith("#")
                or line.startswith("命令：")
                or bool(re.search(r"\b[a-z][a-z0-9]+(?:-[a-z0-9]+){1,5}\b", line))
            )
            if not interesting:
                continue
        else:
            has_cjk = bool(re.search(r"[\u4e00-\u9fff]", line))
            has_signal = (
                "skill" in lowered
                or "openclaw" in lowered
                or "github" in lowered
                or "http://" in lowered
                or "https://" in lowered
                or line.startswith("命令：")
                or bool(re.search(r"\b[a-z][a-z0-9]+(?:-[a-z0-9]+){1,5}\b", line))
            )
            low_value_tokens = [
                "notifications",
                "issues",
                "pull requests",
                "license",
                "stars",
                "forks",
                "sign in",
                "tags",
                "编辑于",
                "鼠标悬停",
                "说点什么",
            ]
            if any(token in lowered for token in low_value_tokens):
                continue
            if re.fullmatch(r"[0-9a-zA-Z\s\-_/]+", line) and not has_signal:
                continue
            if re.search(r"\b[0-9]{2,}\b", line) and not has_signal and not has_cjk:
                continue
            digit_count = sum(ch.isdigit() for ch in line)
            if digit_count >= 4 and len(line) <= 24 and not has_signal:
                continue
            if not has_cjk and not has_signal:
                continue
            if has_cjk and len(line) < 6:
                continue
        if len(line) > 220:
            line = line[:220].rstrip()
        if line not in lines:
            lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def _extract_high_value_ocr_lines(text: str) -> list[str]:
    return _extract_ocr_lines(text, strict=True, limit=25)


def _extract_general_ocr_lines(text: str) -> list[str]:
    return _extract_ocr_lines(text, strict=False, limit=25)


def _should_try_browser_ocr(source_kind: str, source_url: str | None, text: str, config: AppConfig) -> bool:
    if not source_url:
        return False
    lowered_url = source_url.lower()
    reliable_html_domains = (
        "github.com/",
        "docs.openclaw.ai/",
    )
    if any(domain in lowered_url for domain in reliable_html_domains):
        return False
    browser_hosts = config.extractors.browser_ocr_hosts or []
    if any(host in source_url for host in browser_hosts):
        return True
    cleaned = text.strip()
    if not cleaned:
        return True
    if _looks_like_url_only(cleaned, source_url):
        return True
    if _looks_like_legal_footer(cleaned):
        return True
    if source_kind == "mixed":
        return len(cleaned) < int(config.extractors.browser_ocr_min_chars_mixed)
    return len(cleaned) < int(config.extractors.browser_ocr_min_chars_url)


def _is_probable_image_path(value: str) -> bool:
    candidate = value.strip().strip("\"'")
    return bool(re.search(r"\.(?:png|jpg|jpeg|webp|bmp|gif)$", candidate, re.IGNORECASE))


def _resolve_image_path(raw_value: str, output_dir: Path | None = None) -> str | None:
    candidate = raw_value.strip().strip("\"'")
    if not candidate:
        return None
    if candidate.startswith("- "):
        candidate = candidate[2:].strip()
    if _is_probable_image_path(candidate):
        path = Path(candidate).expanduser()
        if not path.is_absolute() and output_dir is not None:
            path = (output_dir / path).resolve()
        return str(path)
    return None


def _parse_keyframe_output(raw: str, output_dir: Path | None = None) -> tuple[list[str], list[str]]:
    image_refs: list[str] = []
    text_hints: list[str] = []
    for line in [item.strip() for item in raw.splitlines() if item.strip()]:
        path = _resolve_image_path(line, output_dir=output_dir)
        if path:
            image_refs.append(path)
            continue
        text_hints.append(line)
    return _dedupe_strings(image_refs, limit=80), _dedupe_strings(text_hints, limit=80)


def _format_seconds_label(value: float) -> str:
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _parse_duration_seconds(value) -> float | None:
    if isinstance(value, (int, float)):
        duration = float(value)
        if duration > 0:
            return duration
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("ms"):
            text = text[:-2]
            try:
                return max(0.0, float(text) / 1000.0)
            except ValueError:
                return None
        if text.endswith("s"):
            text = text[:-1]
        try:
            duration = float(text)
            if duration > 0:
                return duration
        except ValueError:
            return None
    return None


def _canonicalize_video_source_url(url: str | None) -> str | None:
    if not url:
        return url
    source = url.strip()
    if not source:
        return source
    lowered = source.lower()
    if "bilibili.com" in lowered or "b23.tv" in lowered:
        match = re.search(r"(?i)\b(BV[0-9A-Za-z]{10,})\b", source)
        if match:
            return f"https://www.bilibili.com/video/{match.group(1)}"
    return source


def _normalize_video_page_title(title: str | None) -> str:
    value = _normalize_text(title or "")
    if not value:
        return ""
    value = re.sub(r"(?i)[\s_\-|]*(?:哔哩哔哩|bilibili).*$", "", value).strip(" -_|")
    if not value:
        return ""
    if value.lower() in {"哔哩哔哩", "bilibili"}:
        return ""
    return value


def _sanitize_video_page_snapshot_text(text: str, title: str | None = None) -> str:
    cleaned_lines: list[str] = []
    normalized_title = _normalize_video_page_title(title)
    if normalized_title:
        cleaned_lines.append(normalized_title)
    low_value_tokens = (
        "相关推荐",
        "分p",
        "下一条",
        "播放",
        "弹幕",
        "点赞",
        "投币",
        "收藏",
        "转发",
        "关注",
        "评论",
        "排行榜",
        "热门",
        "投稿",
        "立即播放",
    )
    for raw in (text or "").splitlines():
        line = _normalize_text(raw)
        if not line:
            continue
        lowered = line.lower()
        if _looks_like_ui_noise(line) or _looks_like_comment_noise(line):
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", line):
            continue
        if re.fullmatch(r"\d{1,2}:\d{2}(?:\s*/\s*\d{1,2}:\d{2})?", line):
            continue
        if any(token in line for token in low_value_tokens):
            continue
        # Keep only stronger lines to avoid recommendation-feed noise.
        has_signal = (
            "github" in lowered
            or "开源" in line
            or "教程" in line
            or "讲解" in line
            or "安装" in line
            or "部署" in line
            or "实测" in line
            or "项目" in line
            or "openclaw" in lowered
            or "skill" in lowered
            or "http://" in lowered
            or "https://" in lowered
        )
        if not has_signal and re.search(r"(我怎么|这个就是|十年前|笑死|离谱|有没有人|求个|太离谱)", line):
            continue
        if not has_signal and re.search(r"[？?]$", line):
            continue
        if not has_signal and len(line) < 10:
            continue
        if not has_signal and re.fullmatch(r"[A-Za-z0-9\s\-_/.:]+", line):
            continue
        if len(line) > 180:
            line = line[:180].rstrip() + "..."
        if line not in cleaned_lines:
            cleaned_lines.append(line)
        if len(cleaned_lines) >= 8:
            break
    return "\n".join(cleaned_lines).strip()


def _infer_duration_from_timestamps(text: str) -> float | None:
    if not text:
        return None
    max_seconds = 0
    found = False
    # Match mm:ss or hh:mm:ss timestamps.
    for match in re.finditer(r"\b(?:(\d{1,2}):)?([0-5]?\d):([0-5]?\d)\b", text):
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        total = hours * 3600 + minutes * 60 + seconds
        if total > max_seconds:
            max_seconds = total
            found = True
    if not found:
        return None
    return float(max_seconds)


def _parse_video_text_output(raw: str) -> tuple[str, dict[str, object]]:
    text = (raw or "").strip()
    if not text:
        return "", {}
    if not text.startswith("{"):
        inferred = _infer_duration_from_timestamps(text)
        metadata: dict[str, object] = {}
        if inferred is not None:
            metadata["duration_seconds"] = inferred
        return text, metadata

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        inferred = _infer_duration_from_timestamps(text)
        metadata = {}
        if inferred is not None:
            metadata["duration_seconds"] = inferred
        return text, metadata

    if not isinstance(payload, dict):
        return text, {}

    output_text = ""
    for field in ("text", "transcript", "subtitle", "content"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            output_text = value.strip()
            break

    segment_lines: list[str] = []
    segments = payload.get("segments")
    if isinstance(segments, list):
        for item in segments[:1200]:
            if not isinstance(item, dict):
                continue
            segment_text = str(item.get("text", "")).strip()
            if not segment_text:
                continue
            start = _parse_duration_seconds(item.get("start"))
            if start is None:
                start = _parse_duration_seconds(item.get("start_seconds"))
            if start is None:
                start = _parse_duration_seconds(item.get("offset"))
            if start is None:
                segment_lines.append(segment_text)
            else:
                segment_lines.append(f"[{_format_seconds_label(start)}] {segment_text}")
        if not output_text and segment_lines:
            output_text = "\n".join(segment_lines)

    metadata: dict[str, object] = {}
    for field in ("duration_seconds", "duration", "duration_sec"):
        parsed = _parse_duration_seconds(payload.get(field))
        if parsed is not None:
            metadata["duration_seconds"] = parsed
            break
    if "duration_seconds" not in metadata:
        parsed_ms = _parse_duration_seconds(payload.get("duration_ms"))
        if parsed_ms is not None:
            metadata["duration_seconds"] = parsed_ms
    if "duration_seconds" not in metadata and output_text:
        inferred = _infer_duration_from_timestamps(output_text)
        if inferred is not None:
            metadata["duration_seconds"] = inferred

    if segment_lines:
        metadata["timeline_lines"] = segment_lines[:300]
    if isinstance(payload.get("language"), str):
        metadata["language"] = payload["language"]
    if output_text:
        return output_text, metadata
    # Structured payload parsed successfully but no usable text should stay empty
    # (avoid leaking raw JSON into evidence body and false subtitle positives).
    return "", metadata


def _normalize_structured_ocr_output(raw: str) -> str:
    text = (raw or "").strip()
    if not text.startswith("{"):
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(payload, dict):
        return text
    for field in ("text", "content", "transcript"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    segments = payload.get("segments")
    if isinstance(segments, list):
        lines: list[str] = []
        for item in segments[:500]:
            if not isinstance(item, dict):
                continue
            value = str(item.get("text", "")).strip()
            if value:
                lines.append(value)
        if lines:
            return "\n".join(lines)
    return ""


def _ocr_images(image_refs: list[str], command_template: str, *, strict: bool) -> tuple[list[str], list[str]]:
    if not command_template:
        return [], []
    lines: list[str] = []
    warnings: list[str] = []
    for image_ref in image_refs[:30]:
        try:
            raw = _run_template(command_template, input_path=image_ref)
            ocr_text = _normalize_structured_ocr_output(raw)
            extracted = _extract_high_value_ocr_lines(ocr_text) if strict else _extract_general_ocr_lines(ocr_text)
            lines.extend(extracted)
        except Exception as exc:
            warnings.append(f"image_ocr_failed[{image_ref}]: {exc}")
    return _dedupe_strings(lines, limit=120), warnings


def _select_salient_lines(lines: list[str], limit: int = 18) -> list[str]:
    def score(line: str) -> int:
        lowered = line.lower()
        value = 0
        if "github.com/" in lowered or lowered.startswith(("http://", "https://")):
            value += 8
        if "skill" in lowered or "技能" in line:
            value += 6
        if line.startswith(("技能名:", "技能ID:", "命令:", "链接:", "项目:")):
            value += 6
        if re.search(r"\b[a-z][a-z0-9]+(?:-[a-z0-9]+){1,5}\b", line):
            value += 4
        if "openclaw" in lowered:
            value += 3
        noise_tokens = ["notifications", "issues", "pull requests", "sign in", "鼠标悬停", "说点什么"]
        if any(token in lowered for token in noise_tokens):
            value -= 4
        return value

    indexed = [(idx, line.strip()) for idx, line in enumerate(lines) if line.strip()]
    ranked = sorted(indexed, key=lambda item: (score(item[1]), -item[0]), reverse=True)
    picked = sorted(ranked[: max(limit * 2, 20)], key=lambda item: item[0])
    result: list[str] = []
    for _, line in picked:
        if line in result:
            continue
        if len(line) > 180:
            line = line[:180].rstrip()
        result.append(line)
        if len(result) >= limit:
            break
    return result


def _fetch_openclaw_browser_screenshot_ocr(url: str, command_template: str) -> tuple[str, str | None]:
    if not command_template:
        return "", None
    tab = _find_or_open_browser_tab(url)
    if not tab:
        raise RuntimeError("browser tab not found for screenshot OCR")
    screenshot_payload = _run_openclaw_browser_json(
        "screenshot",
        tab["targetId"],
        "--full-page",
    )
    image_path = screenshot_payload.get("path")
    if not image_path:
        raise RuntimeError("screenshot path missing from browser response")
    ocr_text = _run_template(command_template, input_path=image_path)
    lines = _extract_high_value_ocr_lines(ocr_text)
    lines = _select_salient_lines(lines, limit=16)
    return "\n".join(lines).strip(), image_path


class EvidenceExtractor:
    def __init__(self, config: AppConfig, artifacts_dir: Path) -> None:
        self.config = config
        self.artifacts_dir = artifacts_dir
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def extract(self, request: IngestRequest) -> EvidenceBundle:
        if request.source_kind == "pasted_text":
            return self._from_text(request)
        if request.source_kind == "image":
            return self._from_image(request)
        if request.source_kind == "video_url":
            return self._from_video(request)
        if request.platform_hint == "github" or (request.source_url and "github.com" in request.source_url):
            return self._from_github(request)
        if request.source_kind in {"url", "mixed"}:
            return self._from_web(request)
        return self._from_text(request)

    def _from_text(self, request: IngestRequest) -> EvidenceBundle:
        text = (request.raw_text or "").strip()
        metadata: Dict[str, object] = {}
        if text:
            _add_evidence_source(metadata, "user_raw_text")
        signals = _extract_skill_signals(text, request.source_url)
        if signals:
            metadata["signals"] = signals
        metadata = _finalize_evidence_metadata(
            metadata,
            source_kind=request.source_kind,
            source_url=request.source_url,
            text=text,
        )
        return EvidenceBundle(
            source_kind=request.source_kind,
            source_url=request.source_url,
            platform_hint=request.platform_hint,
            title=None,
            text=text,
            evidence_type="raw_text",
            coverage="full" if text else "partial",
            metadata=metadata,
        )

    def _from_web(self, request: IngestRequest) -> EvidenceBundle:
        raw_text = (request.raw_text or "").strip()
        text = "" if _looks_like_url_only(raw_text, request.source_url) else raw_text
        title = None
        request_metadata: Dict[str, object] = {}
        if text:
            _add_evidence_source(request_metadata, "user_raw_text")
        fetch_warnings: list[str] = []
        image_refs: list[str] = list(request.image_refs)
        temp_image_refs: list[str] = []
        fetched_text = ""
        if request.source_url:
            if self.config.extractors.webpage_text_command:
                try:
                    raw = _run_template(self.config.extractors.webpage_text_command, url=request.source_url)
                    parsed = json.loads(raw) if raw.startswith("{") else {"text": raw}
                    title = parsed.get("title") or title
                    fetched_text = str(parsed.get("text", "")).strip()
                    if fetched_text:
                        _add_evidence_source(request_metadata, "webpage_text_command")
                except Exception as exc:
                    fetch_warnings.append(f"webpage_text_command_failed: {exc}")
            if not fetched_text:
                try:
                    if "cloud.tencent.com/developer/article" in request.source_url:
                        html_title, html_text = _fetch_html_document(request.source_url)
                        title, fetched_text = html_title or title, html_text
                        if fetched_text:
                            _add_evidence_source(request_metadata, "web_html_document")
                        step_items = _extract_step_items_from_text(fetched_text or "")
                        if not fetched_text or len(fetched_text) < 900 or not step_items:
                            try:
                                browser_title, browser_text, browser_step_items = _fetch_openclaw_browser_snapshot_with_steps(
                                    request.source_url,
                                    limit=3000,
                                )
                                if browser_text:
                                    title, fetched_text = browser_title or title, browser_text
                                    _add_evidence_source(request_metadata, "browser_snapshot")
                                if browser_step_items:
                                    step_items = browser_step_items
                            except Exception as browser_exc:
                                fetch_warnings.append(f"browser_snapshot_failed: {browser_exc}")
                        if step_items:
                            step_lines: list[str] = []
                            for item in step_items:
                                title_part = item.get("title") or ""
                                detail = item.get("detail") or ""
                                command = item.get("command") or ""
                                line = title_part
                                if detail:
                                    line = f"{line}：{detail}"
                                if command:
                                    line = f"{line}（命令：{command}）" if line else f"命令：{command}"
                                if line:
                                    step_lines.append(line)
                            request_metadata.update({"steps": step_lines, "step_items": step_items})
                    elif "xiaohongshu.com" in request.source_url:
                        try:
                            title, fetched_text = _fetch_openclaw_browser_snapshot(request.source_url, limit=2400)
                            if fetched_text:
                                _add_evidence_source(request_metadata, "browser_snapshot")
                        except Exception as browser_exc:
                            fetch_warnings.append(f"browser_snapshot_failed: {browser_exc}")
                            html_title, html_text = _fetch_html_document(request.source_url)
                            title, fetched_text = html_title or title, html_text
                            if fetched_text:
                                _add_evidence_source(request_metadata, "web_html_document")
                    else:
                        html_title, html_text = _fetch_html_document(request.source_url)
                        title, fetched_text = html_title or title, html_text
                        if fetched_text:
                            _add_evidence_source(request_metadata, "web_html_document")
                except Exception as exc:
                    fetch_warnings.append(f"fetch_failed: {exc}")

        ocr_cmd = _ocr_command(self.config)
        merged_text = _merge_text_blocks(text, fetched_text)
        if request.source_url and ocr_cmd and _should_try_browser_ocr(
            request.source_kind,
            request.source_url,
            merged_text,
            self.config,
        ):
            try:
                ocr_text, screenshot_path = _fetch_openclaw_browser_screenshot_ocr(request.source_url, ocr_cmd)
                if screenshot_path:
                    temp_image_refs.append(screenshot_path)
                if ocr_text:
                    _add_evidence_source(request_metadata, "browser_screenshot_ocr")
                    merged_text = _merge_text_blocks(merged_text, "[OCR补充]\n" + ocr_text)
            except Exception as exc:
                fetch_warnings.append(f"screenshot_ocr_failed: {exc}")

        if request.image_refs and ocr_cmd:
            uploaded_ocr_lines, upload_ocr_warnings = _ocr_images(request.image_refs, ocr_cmd, strict=False)
            fetch_warnings.extend(upload_ocr_warnings)
            if uploaded_ocr_lines:
                _add_evidence_source(request_metadata, "uploaded_image_ocr")
                merged_text = _merge_text_blocks(
                    merged_text,
                    "[上传图片OCR]\n" + "\n".join(_select_salient_lines(uploaded_ocr_lines, limit=16)),
                )
        text = merged_text
        if fetch_warnings:
            request_metadata["fetch_warnings"] = _dedupe_strings(fetch_warnings, limit=20)
        if image_refs:
            request_metadata["image_refs"] = _dedupe_strings(image_refs, limit=40)
        if temp_image_refs:
            request_metadata["temp_image_refs"] = _dedupe_strings(temp_image_refs, limit=40)
        if not text and fetch_warnings:
            text = f"[fetch error] {' | '.join(fetch_warnings[:2])}"
        signals = _extract_skill_signals(text, request.source_url)
        if signals:
            request_metadata["signals"] = signals
        if _looks_like_legal_footer(text):
            text = ""
        request_metadata = _finalize_evidence_metadata(
            request_metadata,
            source_kind=request.source_kind,
            source_url=request.source_url,
            text=text,
        )
        return EvidenceBundle(
            source_kind=request.source_kind,
            source_url=request.source_url,
            platform_hint=request.platform_hint,
            title=title,
            text=text,
            evidence_type="visible_page_text",
            coverage="full" if text else "partial",
            metadata=request_metadata,
        )

    def _from_github(self, request: IngestRequest) -> EvidenceBundle:
        user_text = (request.raw_text or "").strip()
        text = "" if _looks_like_url_only(user_text, request.source_url) else user_text
        title = None
        metadata: Dict[str, object] = {}
        if text:
            _add_evidence_source(metadata, "user_raw_text")
        fetched_text = ""
        if request.source_url and self.config.extractors.github_text_command:
            try:
                raw = _run_template(self.config.extractors.github_text_command, url=request.source_url)
                parsed = json.loads(raw) if raw.startswith("{") else {"text": raw}
                title = parsed.get("title")
                fetched_text = str(parsed.get("text", "")).strip()
                parsed_metadata = parsed.get("metadata", {}) if isinstance(parsed.get("metadata", {}), dict) else {}
                metadata = dict(parsed_metadata)
                if text:
                    _add_evidence_source(metadata, "user_raw_text")
                if fetched_text:
                    _add_evidence_source(metadata, "github_text_command")
            except Exception as exc:
                if not text:
                    text = f"[github extractor error] {exc}"
        elif request.source_url:
            try:
                blob_title, blob_text, blob_meta = _fetch_github_blob_summary(request.source_url)
                if blob_text:
                    title, fetched_text, metadata = blob_title, blob_text, dict(blob_meta)
                    if text:
                        _add_evidence_source(metadata, "user_raw_text")
                    _add_evidence_source(metadata, "github_blob")
                else:
                    api_title, api_text, api_meta = _fetch_github_repo_summary(request.source_url)
                    if api_text:
                        title, fetched_text, metadata = api_title, api_text, dict(api_meta)
                        if text:
                            _add_evidence_source(metadata, "user_raw_text")
                        _add_evidence_source(metadata, "github_api")
                    else:
                        title, fetched_text = _fetch_html_document(request.source_url)
                        if fetched_text:
                            _add_evidence_source(metadata, "web_html_document")
            except Exception as exc:
                if not text:
                    text = f"[github fetch error] {exc}"
        text = _merge_text_blocks(fetched_text, text)
        signals = _extract_skill_signals(text, request.source_url)
        if signals:
            metadata["signals"] = _tighten_github_signals(signals, request.source_url)
        metadata = _finalize_evidence_metadata(
            metadata,
            source_kind=request.source_kind,
            source_url=request.source_url,
            text=text,
        )
        return EvidenceBundle(
            source_kind=request.source_kind,
            source_url=request.source_url,
            platform_hint="github",
            title=title,
            text=text,
            evidence_type="structured_github_text",
            coverage="full" if text else "partial",
            metadata=metadata,
        )

    def _from_image(self, request: IngestRequest) -> EvidenceBundle:
        ocr_cmd = _ocr_command(self.config)
        outputs: List[str] = []
        metadata: Dict[str, object] = {"image_refs": request.image_refs}
        if request.image_refs:
            _add_evidence_source(metadata, "uploaded_image")
        warnings: list[str] = []
        if ocr_cmd and request.image_refs:
            ocr_lines, warnings = _ocr_images(request.image_refs, ocr_cmd, strict=False)
            if ocr_lines:
                _add_evidence_source(metadata, "uploaded_image_ocr")
                outputs.append("\n".join(_select_salient_lines(ocr_lines, limit=18)))
        if request.raw_text:
            _add_evidence_source(metadata, "user_raw_text")
            outputs.append(request.raw_text)
        if warnings:
            metadata["fetch_warnings"] = _dedupe_strings(warnings, limit=20)
        text = _merge_text_blocks(*outputs)
        signals = _extract_skill_signals(text, request.source_url)
        if signals:
            metadata["signals"] = signals
        metadata = _finalize_evidence_metadata(
            metadata,
            source_kind="image",
            source_url=request.source_url,
            text=text,
        )
        return EvidenceBundle(
            source_kind="image",
            source_url=request.source_url,
            platform_hint=request.platform_hint,
            title=None,
            text=text,
            evidence_type="ocr",
            coverage="full" if text else "partial",
            metadata=metadata,
        )

    def _from_video(self, request: IngestRequest) -> EvidenceBundle:
        subtitle_text = ""
        keyframes: List[str] = []
        transcript = None
        metadata: Dict[str, object] = {}
        if request.raw_text:
            _add_evidence_source(metadata, "user_raw_text")
        warnings: list[str] = []
        video_url = _canonicalize_video_source_url(request.source_url)
        if request.source_url and video_url and request.source_url != video_url:
            metadata["raw_source_url"] = request.source_url
            metadata["normalized_source_url"] = video_url
        subtitle_meta: dict[str, object] = {}
        audio_meta: dict[str, object] = {}
        probe_seconds = 0
        if request.video_probe_seconds and request.video_probe_seconds > 0:
            probe_seconds = int(request.video_probe_seconds)
        elif request.dry_run and not request.force_full_video:
            probe_seconds = max(0, int(self.config.execution.dry_run_video_probe_seconds))
        extraction_profile = "full"
        if probe_seconds > 0:
            extraction_profile = "probe"
            metadata["video_probe_seconds"] = probe_seconds
        if request.dry_run:
            extraction_profile = "dry_run_" + extraction_profile
        metadata["video_extraction_profile"] = extraction_profile
        if video_url and self.config.extractors.video_subtitle_command:
            try:
                subtitle_raw = _run_template(
                    self.config.extractors.video_subtitle_command,
                    url=video_url,
                    max_seconds=str(probe_seconds),
                )
                subtitle_text, subtitle_meta = _parse_video_text_output(subtitle_raw)
                if subtitle_text.strip():
                    _add_evidence_source(metadata, "video_subtitles")
            except Exception as exc:
                warnings.append(f"video_subtitle_failed: {exc}")
                subtitle_text = ""
        skip_audio_in_dry_run = (
            request.dry_run and not request.force_full_video and self.config.execution.dry_run_skip_video_audio
        )
        should_run_audio = (
            video_url
            and bool(self.config.extractors.video_audio_command)
            and not skip_audio_in_dry_run
            and (
                self.config.video_accuracy.always_run_audio
                or len(subtitle_text.strip()) < self.config.video_accuracy.audio_when_subtitle_short_chars
            )
        )
        if video_url and self.config.extractors.video_audio_command and should_run_audio:
            try:
                audio_raw = _run_template(
                    self.config.extractors.video_audio_command,
                    url=video_url,
                    max_seconds=str(probe_seconds),
                    api_key=self.config.summarizer.api_key,
                    api_base_url=self.config.summarizer.api_base_url,
                )
                transcript_text, audio_meta = _parse_video_text_output(audio_raw)
                transcript = transcript_text
                if (transcript or "").strip():
                    _add_evidence_source(metadata, "video_audio_asr")
            except Exception as exc:
                warnings.append(f"video_audio_failed: {exc}")
                transcript = None
        elif video_url and self.config.extractors.video_audio_command:
            if skip_audio_in_dry_run:
                metadata["audio_skipped_reason"] = "dry_run_skip_video_audio"
            else:
                metadata["audio_skipped_reason"] = "subtitle_sufficient"
        keyframe_text_hints: list[str] = []
        skip_keyframes_in_dry_run = (
            request.dry_run and not request.force_full_video and self.config.execution.dry_run_skip_video_keyframes
        )
        if video_url and self.config.extractors.video_keyframes_command and not skip_keyframes_in_dry_run:
            output_dir = self.artifacts_dir / request.request_id
            output_dir.mkdir(parents=True, exist_ok=True)
            try:
                raw = _run_template(
                    self.config.extractors.video_keyframes_command,
                    url=video_url,
                    output_path=str(output_dir),
                    max_seconds=str(probe_seconds),
                )
                if raw:
                    keyframes, keyframe_text_hints = _parse_keyframe_output(raw, output_dir=output_dir)
                    if keyframes:
                        _add_evidence_source(metadata, "video_keyframes")
            except Exception as exc:
                warnings.append(f"video_keyframes_failed: {exc}")
                keyframes = []
        elif skip_keyframes_in_dry_run:
            metadata["keyframes_skipped_reason"] = "dry_run_skip_video_keyframes"
        ocr_cmd = _ocr_command(self.config)
        keyframe_ocr_lines: list[str] = []
        if keyframes and ocr_cmd:
            keyframe_ocr_lines, ocr_warnings = _ocr_images(keyframes, ocr_cmd, strict=False)
            warnings.extend(ocr_warnings)
            keyframe_ocr_lines = _select_salient_lines(keyframe_ocr_lines, limit=16)
            if keyframe_ocr_lines:
                _add_evidence_source(metadata, "video_keyframe_ocr")
        include_keyframe_ocr = False
        if keyframe_ocr_lines:
            transcript_len = len((transcript or "").strip())
            has_high_signal = any(
                (
                    "github" in line.lower()
                    or "http://" in line.lower()
                    or "https://" in line.lower()
                    or "skill" in line.lower()
                    or bool(re.search(r"\b[a-z][a-z0-9]+(?:-[a-z0-9]+){1,5}\b", line))
                )
                for line in keyframe_ocr_lines
            )
            include_keyframe_ocr = transcript_len < 160 or has_high_signal
        merged_text = _merge_text_blocks(
            subtitle_text,
            transcript or "",
            request.raw_text or "",
            "\n".join(keyframe_text_hints),
            "[关键帧OCR]\n" + "\n".join(keyframe_ocr_lines) if include_keyframe_ocr else "",
        )
        if video_url and len(merged_text.strip()) < 120:
            snapshot_text = ""
            try:
                page_title, page_text = _fetch_openclaw_browser_snapshot(
                    video_url,
                    limit=2200 if probe_seconds > 0 else 3200,
                )
                if page_title and not metadata.get("page_title"):
                    metadata["page_title"] = page_title
                snapshot_text = _sanitize_video_page_snapshot_text(page_text, page_title)
                if snapshot_text:
                    metadata["video_page_snapshot_used"] = True
                    _add_evidence_source(metadata, "video_page_snapshot")
            except Exception as exc:
                warnings.append(f"video_page_snapshot_failed: {exc}")
            if not snapshot_text:
                try:
                    html_title, html_text = _fetch_html_document(video_url)
                    if html_title and not metadata.get("page_title"):
                        metadata["page_title"] = html_title
                    snapshot_text = _sanitize_video_page_snapshot_text(html_text, html_title)
                    if snapshot_text:
                        metadata["video_html_fallback_used"] = True
                        _add_evidence_source(metadata, "video_html_fallback")
                except Exception as exc:
                    warnings.append(f"video_html_fallback_failed: {exc}")
            if snapshot_text:
                merged_text = _merge_text_blocks(merged_text, "[视频页面补充]\n" + snapshot_text)
        compacted_text, compact_stats = _compact_video_evidence_text(
            merged_text,
            max_lines=max(40, int(self.config.video_accuracy.max_evidence_lines)),
            max_chars=max(1200, int(self.config.video_accuracy.max_evidence_chars)),
        )
        merged_text = compacted_text
        if (
            compact_stats.get("raw_chars", 0) != compact_stats.get("kept_chars", 0)
            or compact_stats.get("raw_lines", 0) != compact_stats.get("kept_lines", 0)
        ):
            metadata["video_text_compacted"] = compact_stats
        if keyframes:
            metadata["temp_image_refs"] = keyframes
            metadata["temp_artifact_dirs"] = [str((self.artifacts_dir / request.request_id).resolve())]
        if keyframe_text_hints:
            metadata["keyframe_text"] = keyframe_text_hints[:30]
        if keyframe_ocr_lines:
            metadata["keyframe_ocr_lines"] = keyframe_ocr_lines[:40]
        if subtitle_meta.get("timeline_lines"):
            metadata["subtitle_timeline_lines"] = subtitle_meta["timeline_lines"]
        if subtitle_meta.get("language"):
            metadata["subtitle_language"] = subtitle_meta["language"]
        if audio_meta.get("timeline_lines"):
            metadata["transcript_timeline_lines"] = audio_meta["timeline_lines"]
        if audio_meta.get("language"):
            metadata["transcript_language"] = audio_meta["language"]
        duration_candidates = [
            _parse_duration_seconds(subtitle_meta.get("duration_seconds")),
            _parse_duration_seconds(audio_meta.get("duration_seconds")),
            _infer_duration_from_timestamps(subtitle_text),
            _infer_duration_from_timestamps(transcript or ""),
        ]
        duration_seconds = max([item for item in duration_candidates if item and item > 0], default=0.0)
        if duration_seconds > 0:
            metadata["video_duration_seconds"] = round(duration_seconds, 3)
        metadata["tracks"] = {
            "has_subtitle": bool(subtitle_text.strip()),
            "has_transcript": bool((transcript or "").strip()),
            "has_keyframes": bool(keyframes),
            "has_keyframe_ocr": bool(keyframe_ocr_lines),
            "audio_mode": "forced" if self.config.video_accuracy.always_run_audio else "subtitle_first",
        }
        if warnings:
            metadata["fetch_warnings"] = _dedupe_strings(warnings, limit=20)
        signals = _extract_skill_signals(merged_text, video_url)
        if signals:
            metadata["signals"] = signals
        metadata = _finalize_evidence_metadata(
            metadata,
            source_kind="video_url",
            source_url=video_url,
            text=merged_text,
        )
        evidence_type = "multimodal_video"
        video_title = str(metadata.get("page_title") or "").strip() if isinstance(metadata, dict) else ""
        return EvidenceBundle(
            source_kind="video_url",
            source_url=video_url,
            platform_hint=request.platform_hint,
            title=video_title or None,
            text=merged_text,
            evidence_type=evidence_type,
            coverage="full" if merged_text else "partial",
            transcript=transcript,
            keyframes=keyframes,
            metadata=metadata,
        )
