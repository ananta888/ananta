from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from flask import current_app
from requests import Response, Session
from requests.exceptions import RequestException, Timeout

from agent.config import settings
from agent.services.private_container_network_policy import (
    AddressResolver,
    PrivateContainerResolutionError,
    pin_private_container_address,
)


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
    internal_token: str | None = None
    max_response_bytes: int = 16 * 1024 * 1024


class VoiceProviderService:
    """Hub-side adapter for the dedicated voice-runtime service."""

    def __init__(
        self,
        *,
        session: Session | None = None,
        resolver: AddressResolver | None = None,
    ) -> None:
        self._session = session if session is not None else Session()
        self._session.trust_env = False
        self._resolver = resolver

    def _resolve_config(self) -> VoiceProviderConfig:
        app_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        raw_voice_cfg = app_cfg.get("voice_runtime") if isinstance(app_cfg, dict) else None
        voice_cfg: dict[str, Any] = dict(raw_voice_cfg) if isinstance(raw_voice_cfg, dict) else {}
        base_url = str(
            voice_cfg.get("base_url") or current_app.config.get("VOICE_RUNTIME_URL") or settings.voice_runtime_url
        ).strip()
        provider = str(
            voice_cfg.get("provider") or current_app.config.get("VOICE_PROVIDER") or settings.voice_provider
        ).strip()
        timeout_sec = int(
            voice_cfg.get("timeout_sec")
            or current_app.config.get("VOICE_TIMEOUT_SEC")
            or settings.voice_timeout_sec
            or 120
        )
        internal_token = (
            str(
                voice_cfg.get("internal_service_token") or current_app.config.get("VOICE_INTERNAL_SERVICE_TOKEN") or ""
            ).strip()
            or None
        )
        if not base_url:
            raise VoiceProviderError(
                code="voice.config_missing",
                message="VOICE_RUNTIME_URL is not configured",
                status_code=500,
                retriable=False,
            )
        normalized_base_url = self._normalize_origin(base_url)
        raw_allowed_origins = str(
            voice_cfg.get("allowed_origins")
            or current_app.config.get("VOICE_RUNTIME_ALLOWED_ORIGINS")
            or settings.voice_runtime_allowed_origins
        )
        allowed_origins = {self._normalize_origin(item) for item in raw_allowed_origins.split(",") if item.strip()}
        if normalized_base_url not in allowed_origins:
            raise VoiceProviderError(
                code="voice.runtime_origin_forbidden",
                message="voice runtime origin is not allowlisted",
                status_code=500,
                retriable=False,
            )
        raw_max_response_bytes = (
            voice_cfg.get("max_response_bytes")
            or current_app.config.get("VOICE_RUNTIME_MAX_RESPONSE_BYTES")
            or settings.voice_runtime_max_response_bytes
        )
        try:
            max_response_bytes = int(raw_max_response_bytes)
        except (TypeError, ValueError) as exc:
            raise VoiceProviderError(
                code="voice.config_invalid",
                message="voice runtime response limit is invalid",
                status_code=500,
                retriable=False,
            ) from exc
        if not 1024 <= max_response_bytes <= 64 * 1024 * 1024:
            raise VoiceProviderError(
                code="voice.config_invalid",
                message="voice runtime response limit is outside its bounds",
                status_code=500,
                retriable=False,
            )
        return VoiceProviderConfig(
            base_url=normalized_base_url,
            provider=provider or "voice-runtime",
            timeout_sec=max(1, timeout_sec),
            internal_token=internal_token,
            max_response_bytes=max_response_bytes,
        )

    def transcribe(
        self,
        *,
        content: bytes,
        filename: str,
        language: str | None = None,
        recognition_context: dict[str, Any] | None = None,
        request_id: str | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        config = self._resolve_config()
        data: dict[str, str] = {}
        if language:
            data["language"] = language
        if recognition_context:
            data["recognition_context_json"] = json.dumps(recognition_context, separators=(",", ":"))
        payload = self._post_multipart(
            config=config,
            path="/v1/audio/transcriptions",
            filename=filename,
            content=content,
            data=data,
            request_id=request_id,
            deadline_seconds=deadline_seconds,
        )
        result = {
            "provider": str(payload.get("provider") or config.provider),
            "model": payload.get("model"),
            "text": str(payload.get("text") or ""),
            "language": payload.get("language"),
            "duration_ms": payload.get("duration_ms"),
            "warnings": list(payload.get("warnings") or []),
        }
        for key in (
            "schema_version",
            "segments",
            "pipeline",
            "confidence",
            "raw_backend",
            "rerun_backend",
            "stages",
            "candidates",
            "selected_candidate_id",
            "fusion_strategy",
            "disagreement_regions",
            "decision_trace",
            "provenance",
            "provenance_valid",
            "request_id",
        ):
            if key in payload:
                result[key] = payload.get(key)
        return result

    def models(self) -> list[dict[str, Any]]:
        config = self._resolve_config()
        payload = self._get_json(config=config, path="/v1/models")
        models = payload.get("models")
        if isinstance(models, list):
            return [item for item in models if isinstance(item, dict)]
        return []

    def capability_catalog(self) -> list[dict[str, Any]]:
        config = self._resolve_config()
        payload = self._get_json(config=config, path="/v1/models")
        catalog = payload.get("capability_catalog")
        return [item for item in (catalog or []) if isinstance(item, dict)]

    def health(self) -> dict[str, Any]:
        config = self._resolve_config()
        payload = self._get_json(config=config, path="/health")
        return payload if isinstance(payload, dict) else {"ok": False}

    def voice_command(
        self,
        *,
        content: bytes,
        filename: str,
        context: dict[str, Any] | None = None,
        request_id: str | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        config = self._resolve_config()
        payload = self._post_multipart(
            config=config,
            path="/v1/audio/chat",
            filename=filename,
            content=content,
            data={"context_json": json.dumps(context)} if context else {},
            request_id=request_id,
            deadline_seconds=deadline_seconds,
        )
        result = {
            "provider": str(payload.get("provider") or config.provider),
            "model": payload.get("model"),
            "text": str(payload.get("text") or ""),
            "transcript": payload.get("transcript"),
            "tool_intent": payload.get("tool_intent"),
        }
        for key in ("segments", "pipeline", "confidence", "raw_backend", "rerun_backend", "stages"):
            if key in payload:
                result[key] = payload.get(key)
        return result

    def create_stream(
        self,
        *,
        filename: str,
        language: str | None,
        media_type: str,
        deadline_seconds: float | None,
        max_audio_seconds: float | None = None,
        requested_session_id: str | None = None,
        recognition_context: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        config = self._resolve_config()
        return self._post_json(
            config=config,
            path="/v1/audio/streams",
            payload={
                "filename": filename,
                "language": language,
                "media_type": media_type,
                "deadline_seconds": deadline_seconds,
                "max_audio_seconds": max_audio_seconds,
                "requested_session_id": requested_session_id,
                "recognition_context": recognition_context,
            },
            request_id=request_id,
            deadline_seconds=deadline_seconds,
        )

    def push_stream_chunk(
        self,
        *,
        runtime_session_id: str,
        chunk_sequence: int,
        content: bytes,
        request_id: str | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        config = self._resolve_config()
        return self._request(
            config=config,
            method="PUT",
            path=f"/v1/audio/streams/{runtime_session_id}/chunks/{chunk_sequence}",
            request_id=request_id,
            data=content,
            content_type="application/octet-stream",
            deadline_seconds=deadline_seconds,
        )

    def finalize_stream(
        self,
        *,
        runtime_session_id: str,
        request_id: str | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        config = self._resolve_config()
        return self._request(
            config=config,
            method="POST",
            path=f"/v1/audio/streams/{runtime_session_id}/finalize",
            request_id=request_id,
            deadline_seconds=deadline_seconds,
        )

    def get_stream(
        self,
        *,
        runtime_session_id: str,
        after_event: int = -1,
        request_id: str | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        config = self._resolve_config()
        return self._get_json(
            config=config,
            path=f"/v1/audio/streams/{runtime_session_id}?after_event={int(after_event)}",
            request_id=request_id,
            deadline_seconds=deadline_seconds,
        )

    def delete_stream(
        self,
        *,
        runtime_session_id: str,
        request_id: str | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        config = self._resolve_config()
        return self._request(
            config=config,
            method="DELETE",
            path=f"/v1/audio/streams/{runtime_session_id}",
            request_id=request_id,
            deadline_seconds=deadline_seconds,
        )

    def _post_multipart(
        self,
        *,
        config: VoiceProviderConfig,
        path: str,
        filename: str,
        content: bytes,
        data: dict[str, str],
        request_id: str | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        endpoint, transport_origin, host_header = self._pinned_endpoint(config, path)
        files = {"file": (filename or "audio", content, "application/octet-stream")}
        effective_read_timeout = float(config.timeout_sec)
        if deadline_seconds is not None:
            effective_read_timeout = min(effective_read_timeout, max(0.1, float(deadline_seconds)))
        timeout = (min(5.0, effective_read_timeout), effective_read_timeout)
        headers = self._headers(config, request_id=request_id, deadline_seconds=deadline_seconds)
        headers["Host"] = host_header
        try:
            response = self._session.request(
                "POST",
                endpoint,
                files=files,
                data=data,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
        except Timeout as exc:
            raise VoiceProviderError(
                code="voice.timeout",
                message="voice runtime timed out",
                status_code=504,
                retriable=True,
            ) from exc
        except RequestException as exc:
            raise VoiceProviderError(
                code="voice.runtime_unavailable",
                message="voice runtime is unavailable",
                status_code=503,
                retriable=True,
            ) from exc

        payload = self._decode_payload(
            response,
            config=config,
            expected_transport_origin=transport_origin,
        )
        if response.status_code >= 400:
            raise self._map_runtime_error(payload, status_code=int(response.status_code))
        return payload

    def _get_json(
        self,
        *,
        config: VoiceProviderConfig,
        path: str,
        request_id: str | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        endpoint, transport_origin, host_header = self._pinned_endpoint(config, path)
        timeout = self._timeout(config, deadline_seconds)
        headers = self._headers(
            config,
            request_id=request_id,
            deadline_seconds=deadline_seconds,
        )
        headers["Host"] = host_header
        try:
            response = self._session.request(
                "GET",
                endpoint,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
        except Timeout as exc:
            raise VoiceProviderError(
                code="voice.timeout",
                message="voice runtime timed out",
                status_code=504,
                retriable=True,
            ) from exc
        except RequestException as exc:
            raise VoiceProviderError(
                code="voice.runtime_unavailable",
                message="voice runtime is unavailable",
                status_code=503,
                retriable=True,
            ) from exc
        payload = self._decode_payload(
            response,
            config=config,
            expected_transport_origin=transport_origin,
        )
        if response.status_code >= 400:
            raise self._map_runtime_error(payload, status_code=int(response.status_code))
        return payload

    def _post_json(
        self,
        *,
        config: VoiceProviderConfig,
        path: str,
        payload: dict[str, Any],
        request_id: str | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        return self._request(
            config=config,
            method="POST",
            path=path,
            request_id=request_id,
            json_payload=payload,
            deadline_seconds=deadline_seconds,
        )

    def _request(
        self,
        *,
        config: VoiceProviderConfig,
        method: str,
        path: str,
        request_id: str | None = None,
        json_payload: dict[str, Any] | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        headers = self._headers(
            config,
            request_id=request_id,
            deadline_seconds=deadline_seconds,
        )
        if content_type:
            headers["Content-Type"] = content_type
        endpoint, transport_origin, host_header = self._pinned_endpoint(config, path)
        headers["Host"] = host_header
        try:
            response = self._session.request(
                method,
                endpoint,
                json=json_payload,
                data=data,
                headers=headers,
                timeout=self._timeout(config, deadline_seconds),
                allow_redirects=False,
                stream=True,
            )
        except Timeout as exc:
            raise VoiceProviderError("voice.timeout", "voice runtime timed out", 504, True) from exc
        except RequestException as exc:
            raise VoiceProviderError("voice.runtime_unavailable", "voice runtime is unavailable", 503, True) from exc
        payload = self._decode_payload(
            response,
            config=config,
            expected_transport_origin=transport_origin,
        )
        if response.status_code >= 400:
            raise self._map_runtime_error(payload, status_code=int(response.status_code))
        return payload

    @staticmethod
    def _headers(
        config: VoiceProviderConfig,
        *,
        request_id: str | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, str]:
        headers = {"X-Request-ID": request_id or f"hub-voice-{uuid.uuid4().hex}"}
        if config.internal_token:
            headers["X-Ananta-Internal-Token"] = config.internal_token
        if deadline_seconds is not None:
            headers["X-Ananta-Deadline-Seconds"] = str(max(0.1, float(deadline_seconds)))
        return headers

    @staticmethod
    def _timeout(config: VoiceProviderConfig, deadline_seconds: float | None) -> tuple[float, float]:
        effective = float(config.timeout_sec)
        if deadline_seconds is not None:
            effective = min(effective, max(0.1, float(deadline_seconds)))
        return min(5.0, effective), effective

    @classmethod
    def _decode_payload(
        cls,
        response: Response,
        *,
        config: VoiceProviderConfig,
        expected_transport_origin: str,
    ) -> dict[str, Any]:
        try:
            if 300 <= int(response.status_code) < 400 or getattr(response, "history", None):
                raise VoiceProviderError(
                    code="voice.redirect_forbidden",
                    message="voice runtime redirects are forbidden",
                    status_code=502,
                    retriable=False,
                )
            response_url = str(getattr(response, "url", "") or config.base_url)
            if cls._url_origin(response_url) != expected_transport_origin:
                raise VoiceProviderError(
                    code="voice.runtime_origin_mismatch",
                    message="voice runtime response origin does not match",
                    status_code=502,
                    retriable=False,
                )
            headers = getattr(response, "headers", {}) or {}
            content_type = str(headers.get("Content-Type") or "").lower()
            if "application/json" not in content_type:
                raise VoiceProviderError(
                    code="voice.invalid_response",
                    message="voice runtime response must be JSON",
                    status_code=502,
                    retriable=False,
                )
            content_length = str(headers.get("Content-Length") or "").strip()
            if content_length and int(content_length) > config.max_response_bytes:
                raise VoiceProviderError(
                    code="voice.response_too_large",
                    message="voice runtime response exceeds its byte limit",
                    status_code=502,
                    retriable=False,
                )
            body = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                body.extend(chunk)
                if len(body) > config.max_response_bytes:
                    raise VoiceProviderError(
                        code="voice.response_too_large",
                        message="voice runtime response exceeds its byte limit",
                        status_code=502,
                        retriable=False,
                    )
            payload = json.loads(body)
        except VoiceProviderError:
            raise
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise VoiceProviderError(
                code="voice.invalid_response",
                message="voice runtime returned an invalid response",
                status_code=502,
                retriable=False,
            ) from exc
        finally:
            response.close()
        if not isinstance(payload, dict):
            raise VoiceProviderError(
                code="voice.invalid_response",
                message="voice runtime returned invalid response type",
                status_code=502,
                retriable=False,
            )
        return payload

    @staticmethod
    def _normalize_origin(value: str) -> str:
        parsed = urlsplit(str(value).strip())
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.port is None
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise VoiceProviderError(
                code="voice.runtime_origin_invalid",
                message="voice runtime origin must be an explicit internal HTTP origin",
                status_code=500,
                retriable=False,
            )
        return VoiceProviderService._url_origin(value)

    @staticmethod
    def _url_origin(value: str) -> str:
        parsed = urlsplit(str(value).strip())
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.port is None
            or parsed.username
            or parsed.password
        ):
            raise VoiceProviderError(
                code="voice.runtime_origin_invalid",
                message="voice runtime URL has an invalid origin",
                status_code=502,
                retriable=False,
            )
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        return urlunsplit((parsed.scheme.lower(), f"{host}:{parsed.port}", "", "", ""))

    def _pinned_endpoint(
        self,
        config: VoiceProviderConfig,
        path: str,
    ) -> tuple[str, str, str]:
        base = urlsplit(config.base_url)
        request_target = urlsplit(str(path or ""))
        if (
            request_target.scheme
            or request_target.netloc
            or request_target.fragment
            or not request_target.path.startswith("/")
        ):
            raise VoiceProviderError(
                code="voice.runtime_path_invalid",
                message="voice runtime request path is invalid",
                status_code=500,
                retriable=False,
            )
        try:
            address = pin_private_container_address(
                str(base.hostname or ""),
                int(base.port or 0),
                resolver=self._resolver,
            )
        except PrivateContainerResolutionError as exc:
            raise VoiceProviderError(
                code=(
                    "voice.runtime_address_forbidden"
                    if exc.reason_code == "worker_address_forbidden"
                    else "voice.runtime_unavailable"
                ),
                message="voice runtime must resolve only to a private container address",
                status_code=500 if exc.reason_code == "worker_address_forbidden" else 503,
                retriable=exc.reason_code != "worker_address_forbidden",
            ) from exc
        netloc = f"[{address}]:{base.port}" if ":" in address else f"{address}:{base.port}"
        transport_origin = urlunsplit(("http", netloc, "", "", ""))
        endpoint = urlunsplit(
            (
                "http",
                netloc,
                request_target.path,
                request_target.query,
                "",
            )
        )
        return endpoint, transport_origin, base.netloc

    @staticmethod
    def _map_runtime_error(payload: dict[str, Any], *, status_code: int) -> VoiceProviderError:
        raw_error = payload.get("error")
        error: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
        code = str(error.get("code") or "voice.backend_error")
        message = str(error.get("message") or "voice runtime returned an error")
        retriable = bool(error.get("retriable")) or status_code in {502, 503, 504}
        return VoiceProviderError(code=code, message=message, status_code=status_code, retriable=retriable)


voice_provider_service = VoiceProviderService()


def get_voice_provider_service() -> VoiceProviderService:
    return voice_provider_service
