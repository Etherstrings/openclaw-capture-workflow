"""Route external video providers and normalize their outputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import re
import select
import shlex
import subprocess
import tempfile
import time
from typing import Any, Dict, Iterable, Optional
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from .config import AppConfig, VideoProviderConfig


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_list(values: Iterable[str], limit: int = 12) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if not text or text in result:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _format_template(value: str, **kwargs: str) -> str:
    try:
        return value.format(**kwargs)
    except Exception:
        return value


def _parse_timestamped_lines(text: str) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    pattern = re.compile(r"^\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s*(.+)$")
    for raw in str(text or "").splitlines():
        line = raw.strip()
        match = pattern.match(line)
        if not match:
            continue
        minute_or_hour = int(match.group(1))
        minute = int(match.group(2))
        second = int(match.group(3) or 0)
        body = match.group(4).strip()
        if not body:
            continue
        if match.group(3) is None:
            start = minute_or_hour * 60 + minute
        else:
            start = minute_or_hour * 3600 + minute * 60 + second
        segments.append({"start": float(start), "end": None, "text": body})
    return segments[:800]


def _deep_extract_text(value: Any, *, keys: set[str] | None = None, limit: int = 80) -> list[str]:
    keys = keys or {
        "title",
        "desc",
        "description",
        "content",
        "text",
        "note_content",
        "display_title",
        "nickname",
        "comment",
        "sub_comment",
    }
    results: list[str] = []

    def visit(item: Any) -> None:
        if len(results) >= limit:
            return
        if isinstance(item, dict):
            for key, value in item.items():
                lowered = str(key).lower()
                if lowered in keys and isinstance(value, str):
                    text = _clean_text(value)
                    if text:
                        results.append(text)
                elif isinstance(value, (dict, list)):
                    visit(value)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return _normalize_list(results, limit=limit)


def _extract_comments(payload: Any, limit: int = 8) -> list[str]:
    comment_keys = {"content", "text", "comment", "sub_comment"}
    results: list[str] = []

    def visit(item: Any) -> None:
        if len(results) >= limit:
            return
        if isinstance(item, dict):
            for key, value in item.items():
                lowered = str(key).lower()
                if lowered in comment_keys and isinstance(value, str):
                    text = _clean_text(value)
                    if text and text not in results:
                        results.append(text)
                elif isinstance(value, (dict, list)):
                    visit(value)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(payload)
    return results[:limit]


def _extract_response_text(result: dict[str, Any]) -> str:
    if isinstance(result.get("structuredContent"), dict):
        structured = result["structuredContent"]
        for key in ("content", "text", "transcript", "subtitle"):
            value = structured.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(structured, ensure_ascii=False)
    content = result.get("content", [])
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                text = str(item.get("text", "")).strip()
                if text:
                    parts.append(text)
        if parts:
            return "\n".join(parts).strip()
    return ""


def _parse_json_or_text(value: str) -> tuple[Any, str]:
    text = str(value or "").strip()
    if not text:
        return None, ""
    try:
        return json.loads(text), text
    except json.JSONDecodeError:
        return None, text


def _stdio_rpc_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8") + body


def _readline_with_timeout(stream, timeout_seconds: int) -> bytes:
    ready, _, _ = select.select([stream], [], [], max(1, int(timeout_seconds)))
    if not ready:
        raise TimeoutError("timed out waiting for MCP response")
    line = stream.readline()
    if not line:
        raise RuntimeError("MCP server closed stdout unexpectedly")
    return line


def _read_stdio_mcp_message(stream, timeout_seconds: int) -> dict[str, Any]:
    headers: dict[str, str] = {}
    while True:
        raw = _readline_with_timeout(stream, timeout_seconds)
        if raw in {b"\r\n", b"\n", b""}:
            break
        line = raw.decode("utf-8", "ignore").strip()
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    content_length = int(headers.get("content-length", "0") or 0)
    if content_length <= 0:
        raise RuntimeError("invalid MCP content-length")
    body = b""
    while len(body) < content_length:
        ready, _, _ = select.select([stream], [], [], max(1, int(timeout_seconds)))
        if not ready:
            raise TimeoutError("timed out reading MCP body")
        chunk = stream.read(content_length - len(body))
        if not chunk:
            raise RuntimeError("MCP server closed body stream unexpectedly")
        body += chunk
    return json.loads(body.decode("utf-8"))


def _read_stdio_json_message(proc: subprocess.Popen, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.time() + max(1, int(timeout_seconds))
    stderr_lines: list[str] = []
    while time.time() < deadline:
        streams = [stream for stream in [proc.stdout, proc.stderr] if stream is not None]
        if not streams:
            raise RuntimeError("stdio JSON MCP process has no readable streams")
        ready, _, _ = select.select(streams, [], [], 1)
        if not ready:
            continue
        for stream in ready:
            if stream is proc.stderr:
                line = proc.stderr.readline()
                if line:
                    stderr_lines.append(line.decode("utf-8", "ignore").strip())
                continue
            line = proc.stdout.readline()
            if not line:
                continue
            text = line.decode("utf-8", "ignore").strip()
            if not text:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                stderr_lines.append(text)
    if stderr_lines:
        raise TimeoutError("timed out waiting for JSON stdio MCP response: " + " | ".join(stderr_lines[-3:]))
    raise TimeoutError("timed out waiting for JSON stdio MCP response")


class StdioMcpToolClient:
    def __init__(self, config: VideoProviderConfig) -> None:
        self.config = config

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.config.command.strip():
            raise RuntimeError("provider command is not configured")
        cmd = [self.config.command, *self.config.args]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        try:
            initialize_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "openclaw_capture_workflow", "version": "0.1.0"},
                },
            }
            if (self.config.transport or "").strip() == "stdio_json":
                proc.stdin.write((json.dumps(initialize_payload, ensure_ascii=False) + "\n").encode("utf-8"))
                proc.stdin.flush()
                _ = _read_stdio_json_message(proc, self.config.timeout_seconds)
                proc.stdin.write(
                    (
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 2,
                                "method": "tools/call",
                                "params": {"name": tool_name, "arguments": arguments},
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    ).encode("utf-8")
                )
                proc.stdin.flush()
                response = _read_stdio_json_message(proc, self.config.timeout_seconds)
            else:
                proc.stdin.write(_stdio_rpc_message(initialize_payload))
                proc.stdin.flush()
                _ = _read_stdio_mcp_message(proc.stdout, self.config.timeout_seconds)
                proc.stdin.write(
                    _stdio_rpc_message(
                        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
                    )
                )
                proc.stdin.flush()
                proc.stdin.write(
                    _stdio_rpc_message(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {"name": tool_name, "arguments": arguments},
                        }
                    )
                )
                proc.stdin.flush()
                response = _read_stdio_mcp_message(proc.stdout, self.config.timeout_seconds)
        finally:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        if not isinstance(response.get("result"), dict):
            raise RuntimeError("invalid MCP tool result")
        return response["result"]


class HttpMcpToolClient:
    def __init__(self, config: VideoProviderConfig) -> None:
        self.config = config

    def _post_json(self, payload: dict[str, Any], session_id: str = "") -> tuple[dict[str, Any], str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        req = urlrequest.Request(
            self.config.http_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                next_session = resp.headers.get("Mcp-Session-Id", session_id)
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"http MCP request failed: {exc}") from exc
        if raw.lstrip().startswith("data:"):
            lines = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
            raw = lines[-1] if lines else ""
        payload_obj = json.loads(raw)
        return payload_obj, next_session

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.config.http_url.strip():
            raise RuntimeError("provider http_url is not configured")
        session_id = ""
        init_payload = {"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1}
        _, session_id = self._post_json(init_payload, session_id=session_id)
        call_payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
            "id": 2,
        }
        response, _ = self._post_json(call_payload, session_id=session_id)
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        if not isinstance(response.get("result"), dict):
            raise RuntimeError("invalid HTTP MCP tool result")
        return response["result"]


@dataclass
class VideoProviderBundle:
    provider: str
    transcript_text: str = ""
    subtitle_text: str = ""
    analysis_text: str = ""
    segments: list[dict[str, object]] = field(default_factory=list)
    viewer_feedback: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    capabilities: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def has_speech_track(self) -> bool:
        return bool(self.segments or self.transcript_text.strip() or self.subtitle_text.strip())

    def merge(self, other: "VideoProviderBundle") -> "VideoProviderBundle":
        if self.has_speech_track():
            provider = self.provider or other.provider
        elif other.has_speech_track():
            provider = other.provider or self.provider
        else:
            provider = self.provider or other.provider
        transcript_text = self.transcript_text if len(self.transcript_text) >= len(other.transcript_text) else other.transcript_text
        subtitle_text = self.subtitle_text if len(self.subtitle_text) >= len(other.subtitle_text) else other.subtitle_text
        analysis_text = self.analysis_text if len(self.analysis_text) >= len(other.analysis_text) else other.analysis_text
        segments = self.segments or other.segments
        viewer_feedback = _normalize_list([*self.viewer_feedback, *other.viewer_feedback], limit=12)
        metadata = dict(self.metadata)
        metadata.update(other.metadata)
        capabilities = dict(self.capabilities)
        for key, value in other.capabilities.items():
            capabilities[key] = bool(capabilities.get(key)) or bool(value)
        warnings = _normalize_list([*self.warnings, *other.warnings], limit=20)
        return VideoProviderBundle(
            provider=provider,
            transcript_text=transcript_text,
            subtitle_text=subtitle_text,
            analysis_text=analysis_text,
            segments=segments,
            viewer_feedback=viewer_feedback,
            metadata=metadata,
            capabilities=capabilities,
            warnings=warnings,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VideoProviderAttempt:
    provider: str
    status: str
    message: str = ""
    capabilities: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _language_hint(requested_output_lang: str) -> str:
    value = str(requested_output_lang or "").lower()
    if value.startswith("zh"):
        return "zh-Hans"
    if value.startswith("en"):
        return "en"
    return "en"


def _extract_xiaohongshu_ids(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    feed_id = ""
    for segment in parsed.path.split("/"):
        text = segment.strip()
        if len(text) >= 8 and text not in {"explore", "discovery", "item"}:
            feed_id = text
            break
    token = parse_qs(parsed.query).get("xsec_token", [""])[0].strip()
    return feed_id, token


class VideoProviderRouter:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def resolve_order(self, url: str | None, platform_hint: str | None) -> list[str]:
        if not self.config.video_provider_routing.enabled:
            return ["local"]
        lowered = str(url or "").lower()
        hint = str(platform_hint or "").lower()
        if "bilibili.com" in lowered or hint == "bilibili":
            return list(self.config.video_provider_routing.bilibili_order)
        if "xiaohongshu.com" in lowered or hint == "xiaohongshu":
            return list(self.config.video_provider_routing.xiaohongshu_order)
        if "youtube.com" in lowered or "youtu.be" in lowered or hint == "youtube":
            return list(self.config.video_provider_routing.youtube_order)
        return ["local"]

    def route(
        self,
        *,
        url: str | None,
        platform_hint: str | None,
        requested_output_lang: str,
    ) -> tuple[VideoProviderBundle | None, list[VideoProviderAttempt]]:
        if not url:
            return None, []
        bundle: VideoProviderBundle | None = None
        attempts: list[VideoProviderAttempt] = []
        for provider_name in self.resolve_order(url, platform_hint):
            if provider_name == "local":
                break
            result_bundle, attempt = self._attempt_provider(
                provider_name,
                url=url,
                requested_output_lang=requested_output_lang,
            )
            attempts.append(attempt)
            if result_bundle is None:
                continue
            bundle = result_bundle if bundle is None else bundle.merge(result_bundle)
            if bundle.has_speech_track():
                break
        return bundle, attempts

    def _provider_config(self, provider_name: str) -> VideoProviderConfig | None:
        routing = self.config.video_provider_routing
        return {
            "bilibili_mcp": routing.bilibili_mcp,
            "xiaohongshu_mcp": routing.xiaohongshu_mcp,
            "youtube_transcript_mcp": routing.youtube_transcript_mcp,
            "vidscribe": routing.vidscribe,
        }.get(provider_name)

    def _attempt_provider(
        self,
        provider_name: str,
        *,
        url: str,
        requested_output_lang: str,
    ) -> tuple[VideoProviderBundle | None, VideoProviderAttempt]:
        cfg = self._provider_config(provider_name)
        if cfg is None:
            return None, VideoProviderAttempt(provider=provider_name, status="skipped", message="provider config missing")
        if not cfg.enabled:
            return None, VideoProviderAttempt(provider=provider_name, status="skipped", message="provider disabled")
        try:
            if provider_name == "bilibili_mcp":
                bundle = self._from_bilibili_mcp(cfg, url)
            elif provider_name == "youtube_transcript_mcp":
                bundle = self._from_youtube_transcript_mcp(cfg, url, requested_output_lang)
            elif provider_name == "xiaohongshu_mcp":
                bundle = self._from_xiaohongshu_http_mcp(cfg, url)
            elif provider_name == "vidscribe":
                bundle = self._from_command_provider(cfg, url)
            else:
                raise RuntimeError(f"unsupported provider: {provider_name}")
        except Exception as exc:
            return None, VideoProviderAttempt(provider=provider_name, status="failed", message=str(exc))
        if bundle is None:
            return None, VideoProviderAttempt(provider=provider_name, status="failed", message="provider returned empty bundle")
        return bundle, VideoProviderAttempt(
            provider=provider_name,
            status="ok",
            message="speech" if bundle.has_speech_track() else "metadata_only",
            capabilities=bundle.capabilities,
            warnings=bundle.warnings,
        )

    def _from_bilibili_mcp(self, cfg: VideoProviderConfig, url: str) -> VideoProviderBundle:
        result = StdioMcpToolClient(cfg).call_tool(cfg.tool_name or "extract_bilibili_complete_content", {"video_url": url})
        raw_text = _extract_response_text(result)
        payload, fallback_text = _parse_json_or_text(raw_text)
        if isinstance(payload, dict):
            transcript_text = _clean_text(payload.get("audio_text"))
            subtitle_text = _clean_text(payload.get("subtitles"))
            analysis_text = _clean_text(payload.get("combined_analysis"))
            capabilities = {
                "has_subtitle": bool(subtitle_text),
                "has_transcript": bool(transcript_text),
                "has_timed_segments": False,
                "has_viewer_feedback": False,
            }
            return VideoProviderBundle(
                provider="bilibili_mcp",
                transcript_text=transcript_text,
                subtitle_text=subtitle_text,
                analysis_text=analysis_text,
                metadata={"provider_payload": payload},
                capabilities=capabilities,
            )
        return VideoProviderBundle(
            provider="bilibili_mcp",
            transcript_text=fallback_text,
            capabilities={
                "has_subtitle": False,
                "has_transcript": bool(fallback_text),
                "has_timed_segments": False,
                "has_viewer_feedback": False,
            },
        )

    def _from_youtube_transcript_mcp(
        self,
        cfg: VideoProviderConfig,
        url: str,
        requested_output_lang: str,
    ) -> VideoProviderBundle:
        result = StdioMcpToolClient(cfg).call_tool(
            cfg.tool_name or "get_transcript",
            {
                "url": url,
                "lang": _language_hint(requested_output_lang),
                "include_timestamps": True,
                "strip_ads": True,
            },
        )
        transcript_text = ""
        metadata: dict[str, object] = {}
        if isinstance(result.get("structuredContent"), dict):
            structured = result["structuredContent"]
            transcript_text = str(structured.get("content", "")).strip()
            meta_text = str(structured.get("meta", "")).strip()
            if meta_text:
                metadata["provider_meta"] = meta_text
        if not transcript_text:
            transcript_text = _extract_response_text(result)
        segments = _parse_timestamped_lines(transcript_text)
        return VideoProviderBundle(
            provider="youtube_transcript_mcp",
            transcript_text=transcript_text,
            segments=segments,
            metadata=metadata,
            capabilities={
                "has_subtitle": False,
                "has_transcript": bool(transcript_text),
                "has_timed_segments": bool(segments),
                "has_viewer_feedback": False,
            },
        )

    def _from_xiaohongshu_http_mcp(self, cfg: VideoProviderConfig, url: str) -> VideoProviderBundle:
        feed_id, xsec_token = _extract_xiaohongshu_ids(url)
        if not feed_id or not xsec_token:
            raise RuntimeError("xiaohongshu provider requires feed_id and xsec_token in source_url")
        result = HttpMcpToolClient(cfg).call_tool(
            cfg.tool_name or "get_feed_detail",
            {"feed_id": feed_id, "xsec_token": xsec_token},
        )
        structured = result.get("structuredContent")
        payload = structured if structured is not None else result
        details = _deep_extract_text(payload, limit=60)
        viewer_feedback = _extract_comments(payload, limit=8)
        text_parts = details[:12]
        return VideoProviderBundle(
            provider="xiaohongshu_mcp",
            analysis_text="\n".join(text_parts),
            viewer_feedback=viewer_feedback,
            metadata={"provider_payload": payload},
            capabilities={
                "has_subtitle": False,
                "has_transcript": False,
                "has_timed_segments": False,
                "has_viewer_feedback": bool(viewer_feedback),
            },
        )

    def _from_command_provider(self, cfg: VideoProviderConfig, url: str) -> VideoProviderBundle:
        if not cfg.command.strip():
            raise RuntimeError("command provider is not configured")
        args = [_format_template(arg, url=url) for arg in cfg.args]
        command = [_format_template(cfg.command, url=url), *args]
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=max(1, int(cfg.timeout_seconds)),
        )
        payload, fallback_text = _parse_json_or_text(completed.stdout)
        if isinstance(payload, dict):
            transcript_text = _clean_text(payload.get("transcript") or payload.get("text") or payload.get("content"))
            subtitle_text = _clean_text(payload.get("subtitle"))
            analysis_text = _clean_text(payload.get("summary") or payload.get("analysis"))
            segments = payload.get("segments", [])
            if not isinstance(segments, list):
                segments = []
            return VideoProviderBundle(
                provider="vidscribe",
                transcript_text=transcript_text,
                subtitle_text=subtitle_text,
                analysis_text=analysis_text,
                segments=[item for item in segments[:800] if isinstance(item, dict)],
                metadata={"provider_payload": payload},
                capabilities={
                    "has_subtitle": bool(subtitle_text),
                    "has_transcript": bool(transcript_text),
                    "has_timed_segments": bool(segments),
                    "has_viewer_feedback": False,
                },
            )
        return VideoProviderBundle(
            provider="vidscribe",
            transcript_text=fallback_text,
            capabilities={
                "has_subtitle": False,
                "has_transcript": bool(fallback_text),
                "has_timed_segments": bool(_parse_timestamped_lines(fallback_text)),
                "has_viewer_feedback": False,
            },
        )
