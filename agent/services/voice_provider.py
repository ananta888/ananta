from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import requests
from flask import current_app

from agent.config import settings


@dataclass(frozen=True)
class VoiceProviderError(Exception):
    code: str
    message: str
    status_code: int
    retriable: bool = False


@dataclass(frozen=True)
class VoiceProviderConfig:
    base_url: str
    provider: str
    timeout_sec: int


class VoiceProviderService:
    """Hub-side adapter for the dedicated voice-runtime service."""

    def _resolve_config(self) -> VoiceProviderConfig:
        app_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        voice_cfg = app_cfg.get("voice_runtime") if isinstance(app_cfg.get("voice_runtime"), dict) else {}
        base_url = str(voice_cfg.get("base_url") or current_app.config.get("VOICE_RUNTIME_URL") or settings.voice_runtime_url).strip()
        provider = str(voice_cfg.get("provider") or current_app.config.get("VOICE_PROVIDER") or settings.voice_provider).strip()
        timeout_sec = int(voice_cfg.get("timeout_sec") or current_app.config.get("VOICE_TIMEOUT_SEC") or settings.voice_timeout_sec or 120)
        if not base_url:
            raise VoiceProviderError(
                code="voice.config_missing",
                message="VOICE_RUNTIME_URL is not configured",
                status_code=500,
                retriable=False,
            )
        return VoiceProviderConfig(base_url=base_url.rstrip("/"), provider=provider or "voice-runtime", timeout_sec=max(1, timeout_sec))

    def transcribe(self, *, content: bytes, filename: str, language: str | None = None) -> dict[str, Any]:
        config = self._resolve_config()
        data: dict[str, str] = {}
        if language:
            data["language"] = language
        payload = self._post_multipart(
            config=config,
            path="/v1/audio/transcriptions",
            filename=filename,
            content=content,
            data=data,
        )
        return {
            "provider": str(payload.get("provider") or config.provider),
            "model": payload.get("model"),
            "text": str(payload.get("text") or ""),
            "language": payload.get("language"),
            "duration_ms": payload.get("duration_ms"),
            "warnings": list(payload.get("warnings") or []),
        }

    def voice_command(self, *, content: bytes, filename: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        config = self._resolve_config()
        payload = self._post_multipart(
            config=config,
            path="/v1/audio/chat",
            filename=filename,
            content=content,
            data={"context_json": json.dumps(context)} if context else {},
        )
        return {
            "provider": str(payload.get("provider") or config.provider),
            "model": payload.get("model"),
            "text": str(payload.get("text") or ""),
            "transcript": payload.get("transcript"),
            "tool_intent": payload.get("tool_intent"),
        }

    def _post_multipart(
        self,
        *,
        config: VoiceProviderConfig,
        path: str,
        filename: str,
        content: bytes,
        data: dict[str, str],
    ) -> dict[str, Any]:
        endpoint = f"{config.base_url}{path}"
        files = {"file": (filename or "audio", content, "application/octet-stream")}
        timeout = (5, config.timeout_sec)
        try:
            response = requests.post(endpoint, files=files, data=data, timeout=timeout)
        except requests.Timeout as exc:
            raise VoiceProviderError(
                code="voice.timeout",
                message=f"voice runtime timeout: {exc}",
                status_code=504,
                retriable=True,
            ) from exc
        except requests.RequestException as exc:
            raise VoiceProviderError(
                code="voice.runtime_unavailable",
                message=f"voice runtime unavailable: {exc}",
                status_code=503,
                retriable=True,
            ) from exc

        payload = self._decode_payload(response)
        if response.status_code >= 400:
            raise self._map_runtime_error(payload, status_code=int(response.status_code))
        return payload

    @staticmethod
    def _decode_payload(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise VoiceProviderError(
                code="voice.invalid_response",
                message=f"voice runtime returned non-json payload: {exc}",
                status_code=502,
                retriable=False,
            ) from exc
        if not isinstance(payload, dict):
            raise VoiceProviderError(
                code="voice.invalid_response",
                message="voice runtime returned invalid response type",
                status_code=502,
                retriable=False,
            )
        return payload

    @staticmethod
    def _map_runtime_error(payload: dict[str, Any], *, status_code: int) -> VoiceProviderError:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(error.get("code") or "voice.backend_error")
        message = str(error.get("message") or "voice runtime returned an error")
        retriable = bool(error.get("retriable")) or status_code in {502, 503, 504}
        return VoiceProviderError(code=code, message=message, status_code=status_code, retriable=retriable)


voice_provider_service = VoiceProviderService()


def get_voice_provider_service() -> VoiceProviderService:
    return voice_provider_service
