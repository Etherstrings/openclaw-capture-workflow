"""Top-level URL analysis orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib import request as urlrequest
from urllib.parse import urlparse

from ..config import AppConfig
from .cleanup import cleanup_job_temp_dir, create_job_temp_dir
from .dom_extract import extract_content
from .llm import OpenAIResponsesClient
from .models import AnalysisOutcome, StructuredDocument
from .render import BrowserBackend, PinchTabBackend, PlaywrightBackend
from .video import process_videos


def _validate_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an absolute http(s) URL")
    return url.strip()


def _resolve_temp_root(config: AppConfig, state_dir: Path) -> Path:
    temp_root = Path(config.analysis.temp_root)
    if not temp_root.is_absolute():
        temp_root = state_dir / temp_root
    temp_root.mkdir(parents=True, exist_ok=True)
    return temp_root


def _select_backend(config: AppConfig) -> BrowserBackend:
    backend_name = (config.analysis.browser_backend or "playwright").strip().lower()
    if backend_name == "playwright":
        return PlaywrightBackend()
    if backend_name == "pinchtab":
        return PinchTabBackend(base_url=config.analysis.pinchtab_base_url)
    raise ValueError(f"unsupported browser backend: {backend_name}")


def _should_try_pinchtab(config: AppConfig, chosen_backend: BrowserBackend) -> bool:
    return bool(config.analysis.pinchtab_base_url.strip()) and chosen_backend.__class__.__name__ != "PinchTabBackend"


def _download_images(extracted: ExtractedContent, temp_dir: Path, max_images: int) -> list[str]:
    warnings: list[str] = []
    images_dir = temp_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(extracted.images[:max_images], start=1):
        try:
            suffix = Path(urlparse(item.result.src).path).suffix or ".jpg"
            output_path = images_dir / f"image-{index}{suffix}"
            req = urlrequest.Request(item.result.src, headers={"User-Agent": "Mozilla/5.0 OpenClawCaptureWorkflow/0.1"})
            with urlrequest.urlopen(req, timeout=min(15, max(5, 5))) as response:
                output_path.write_bytes(response.read())
            item.local_path = output_path
        except Exception as exc:
            warnings.append(f"image_download_failed:{item.result.src}:{exc}")
    return warnings


def _extractor_only_document(extracted: ExtractedContent) -> StructuredDocument:
    summary_parts = []
    if extracted.sections:
        for section in extracted.sections[:2]:
            text = section.content.strip()
            if text:
                summary_parts.append(text)
    if not summary_parts and extracted.main_text.strip():
        summary_parts.append(extracted.main_text.strip())
    summary = " ".join(summary_parts).strip()
    if not summary:
        summary = "页面渲染成功，但未能从模型获取结构化总结，已返回本地抽取结果。"
    elif len(summary) > 220:
        summary = summary[:220].rstrip() + "..."
    return StructuredDocument(
        title=extracted.title or "Untitled page",
        summary=summary,
        sections=extracted.sections,
        images=[item.result for item in extracted.images],
        videos=[item.result for item in extracted.videos],
        tables=extracted.tables,
    )


def analyze_url(
    url: str,
    requested_output_lang: str,
    config: AppConfig,
    state_dir: Path,
    backend: Optional[BrowserBackend] = None,
    llm_client: Optional[OpenAIResponsesClient] = None,
    auto_case_sink: Optional[Path] = None,
    auto_case_source_kind: str = "url",
    auto_case_raw_text: Optional[str] = None,
    auto_case_platform_hint: Optional[str] = None,
) -> AnalysisOutcome:
    validated_url = _validate_url(url)
    temp_root = _resolve_temp_root(config, state_dir)
    _, job_dir = create_job_temp_dir(temp_root)
    warnings: list[str] = []
    try:
        chosen_backend = backend or _select_backend(config)
        timeout_seconds = max(5, int(config.analysis.page_timeout_seconds))
        try:
            render_result = chosen_backend.render(
                validated_url,
                temp_dir=job_dir,
                timeout_seconds=timeout_seconds,
            )
            extracted = extract_content(
                render_result,
                max_images=max(1, int(config.analysis.max_images)),
                max_videos=max(1, int(config.analysis.max_videos)),
                max_tables=max(1, int(config.analysis.max_tables)),
            )
        except Exception as exc:
            if not _should_try_pinchtab(config, chosen_backend):
                raise
            warnings.append(f"playwright_render_failed_fallback_to_pinchtab:{exc}")
            pinch_backend = PinchTabBackend(base_url=config.analysis.pinchtab_base_url)
            render_result = pinch_backend.render(
                validated_url,
                temp_dir=job_dir,
                timeout_seconds=timeout_seconds,
            )
            extracted = extract_content(
                render_result,
                max_images=max(1, int(config.analysis.max_images)),
                max_videos=max(1, int(config.analysis.max_videos)),
                max_tables=max(1, int(config.analysis.max_tables)),
            )

        if _should_try_pinchtab(config, chosen_backend) and len(extracted.main_text.strip()) < 160:
            warnings.append("playwright_text_short_fallback_to_pinchtab")
            pinch_backend = PinchTabBackend(base_url=config.analysis.pinchtab_base_url)
            pinch_render = pinch_backend.render(
                validated_url,
                temp_dir=job_dir,
                timeout_seconds=timeout_seconds,
            )
            pinch_extracted = extract_content(
                pinch_render,
                max_images=max(1, int(config.analysis.max_images)),
                max_videos=max(1, int(config.analysis.max_videos)),
                max_tables=max(1, int(config.analysis.max_tables)),
            )
            if len(pinch_extracted.main_text.strip()) > len(extracted.main_text.strip()):
                render_result = pinch_render
                extracted = pinch_extracted

        warnings.extend(_download_images(extracted, job_dir, max_images=config.analysis.max_images))
        processed_videos, video_warnings = process_videos(
            extracted.videos,
            temp_dir=job_dir,
            max_video_frames=max(1, int(config.analysis.max_video_frames)),
            config=config,
        )
        extracted.videos = processed_videos
        warnings.extend(video_warnings)

        client = llm_client or OpenAIResponsesClient(config)
        try:
            document = client.generate_document(
                extracted=extracted,
                requested_output_lang=requested_output_lang,
                screenshot_path=render_result.screenshot_path,
            )
        except Exception as exc:
            warnings.append(f"llm_generation_failed:{exc}")
            document = _extractor_only_document(extracted)

        outcome = AnalysisOutcome(document=document, warnings=warnings)
        if auto_case_sink is not None:
            from ..iterative_cases import maybe_record_auto_case

            maybe_record_auto_case(
                auto_case_sink,
                source_kind=auto_case_source_kind,
                source_url=validated_url,
                raw_text=auto_case_raw_text,
                platform_hint=auto_case_platform_hint,
                warnings=warnings,
                coverage="full" if document.sections else "partial",
                labels=["analyzer", auto_case_source_kind] + ([auto_case_platform_hint] if auto_case_platform_hint else []),
            )
        return outcome
    finally:
        cleanup_job_temp_dir(job_dir)
