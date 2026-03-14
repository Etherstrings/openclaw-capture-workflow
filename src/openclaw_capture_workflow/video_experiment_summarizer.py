"""Gemini-focused video summary experiments via AiHubMix."""

from __future__ import annotations

import json
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from .config import VideoSummaryConfig
from .models import EvidenceBundle, SummaryResult
from .summarizer import PROMPT, _build_video_prompt_context, _validate_and_normalize_summary


class AiHubMixGeminiSummarizer:
    def __init__(self, config: VideoSummaryConfig) -> None:
        self.config = config

    def summarize(self, evidence: EvidenceBundle) -> SummaryResult:
        errors: list[str] = []
        for model in [self.config.model, self.config.fallback_model]:
            try:
                raw = self._request(model, evidence)
                summary = SummaryResult.from_json(raw)
                return _validate_and_normalize_summary(summary, evidence)
            except Exception as exc:
                errors.append(f"{model}:{exc}")
        raise RuntimeError("gemini summarizer failed: " + " | ".join(errors))

    def _request(self, model: str, evidence: EvidenceBundle) -> str:
        if (self.config.transport or "openai_compat").strip() == "native":
            return self._request_native(model, evidence)
        return self._request_openai_compat(model, evidence)

    def _request_openai_compat(self, model: str, evidence: EvidenceBundle) -> str:
        video_context = _build_video_prompt_context(evidence)
        payload = {
            "model": model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "source_kind": evidence.source_kind,
                            "source_url": evidence.source_url,
                            "platform_hint": evidence.platform_hint,
                            "title": evidence.title,
                            "evidence_type": evidence.evidence_type,
                            "coverage": evidence.coverage,
                            "text": evidence.text,
                            "transcript": evidence.transcript,
                            "metadata": evidence.metadata,
                            **video_context,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        req = urlrequest.Request(
            url=f"{self.config.api_base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"gemini compat request failed: {exc}") from exc
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"unexpected gemini compat response: {body}") from exc

    def _request_native(self, model: str, evidence: EvidenceBundle) -> str:
        base = self.config.api_base_url.rstrip("/")
        if not base.endswith("/gemini"):
            base = base + "/gemini"
        video_context = _build_video_prompt_context(evidence)
        payload = {
            "system_instruction": {"parts": [{"text": PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "source_kind": evidence.source_kind,
                                    "source_url": evidence.source_url,
                                    "platform_hint": evidence.platform_hint,
                                    "title": evidence.title,
                                    "evidence_type": evidence.evidence_type,
                                    "coverage": evidence.coverage,
                                    "text": evidence.text,
                                    "transcript": evidence.transcript,
                                    "metadata": evidence.metadata,
                                    **video_context,
                                },
                                ensure_ascii=False,
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        req = urlrequest.Request(
            url=f"{base}/v1beta/models/{model}:generateContent?key={self.config.api_key}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"gemini native request failed: {exc}") from exc
        try:
            return body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected gemini native response: {body}") from exc
