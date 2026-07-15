"""Execution-only corrector engines for centrally configured local LLM providers."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

import requests

from ananta_contracts.voice_corrector_worker import VoiceCorrectorWorkerRequest
from worker.runtime.generative_corrector_engine import (
    GenerativeCorrectorEngine,
    GenerativeCorrectorEngineResult,
    corrector_system_message,
    parse_corrector_output,
)

_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_QUALIFIED_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,191}$")


@dataclass(frozen=True)
class CorrectorProviderEndpoint:
    provider_id: str
    base_url: str
    api_key: str | None = None

    def __post_init__(self) -> None:
        provider_id = str(self.provider_id or "").strip().lower()
        if not _PROVIDER_RE.fullmatch(provider_id):
            raise ValueError("corrector provider identifier is invalid")
        normalized = _normalize_base_url(provider_id, self.base_url)
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "base_url", normalized)
        object.__setattr__(self, "api_key", str(self.api_key or "").strip() or None)


class ProviderGenerativeCorrectorEngine:
    """Invoke one admin-configured provider; request data never selects an endpoint."""

    def __init__(
        self,
        endpoints: Sequence[CorrectorProviderEndpoint],
        *,
        session: requests.Session | None = None,
        discovery_timeout_seconds: float = 0.2,
        response_max_bytes: int = 1024 * 1024,
        discovery_ttl_seconds: float = 15.0,
        max_output_tokens: int = 1024,
    ) -> None:
        self._endpoints = {entry.provider_id: entry for entry in endpoints}
        if not self._endpoints:
            raise ValueError("at least one corrector provider endpoint is required")
        # Production requests use one short-lived Session per invocation. A
        # requests.Session is mutable and not documented as thread-safe; the
        # worker may run correction and background discovery concurrently.
        # The injectable session remains a deterministic unit-test seam.
        self._injected_session = session
        if self._injected_session is not None:
            self._injected_session.trust_env = False
        self._discovery_timeout = max(0.2, min(float(discovery_timeout_seconds), 5.0))
        self._response_max_bytes = max(4096, min(int(response_max_bytes), 2 * 1024 * 1024))
        self._discovery_ttl = max(0.0, min(float(discovery_ttl_seconds), 3600.0))
        self._max_output_tokens = max(16, min(int(max_output_tokens), 4096))
        self._catalog_lock = threading.Lock()
        self._catalog_refreshing = False
        self._catalog_checked_at = 0.0
        self._model_ids: tuple[str, ...] = ()
        self._model_revisions: dict[str, str] = {}
        self._ready_provider_ids: tuple[str, ...] = ()

    @property
    def engine_id(self) -> str:
        return "provider-http"

    @property
    def provider_ids(self) -> tuple[str, ...]:
        """Return endpoints configured by the administrator, reachable or not."""

        return tuple(sorted(self._endpoints))

    @property
    def ready_provider_ids(self) -> tuple[str, ...]:
        """Return providers whose latest bounded discovery request succeeded."""

        self._refresh_catalog_if_needed()
        return self._ready_provider_ids

    @property
    def model_ids(self) -> tuple[str, ...]:
        self._refresh_catalog_if_needed()
        return self._model_ids

    def health_snapshot(self) -> dict[str, tuple[str, ...]]:
        """Return one atomic readiness/catalog view for the worker health API."""

        self._refresh_catalog_if_needed()
        with self._catalog_lock:
            return {
                "model_ids": self._model_ids,
                "provider_ids": self.provider_ids,
                "ready_provider_ids": self._ready_provider_ids,
            }

    def supports_model(self, model_id: str) -> bool:
        try:
            provider_id, raw_model = _split_model_id(model_id)
        except ValueError:
            return False
        return provider_id in self._endpoints and raw_model.casefold() != "auto"

    def correct(self, request: VoiceCorrectorWorkerRequest) -> GenerativeCorrectorEngineResult:
        provider_id, raw_model = _split_model_id(request.model_id)
        endpoint = self._endpoints.get(provider_id)
        if endpoint is None or raw_model.casefold() == "auto":
            raise LookupError("requested provider corrector model is not allowlisted")
        remaining_seconds = (request.deadline_epoch_ms - time.time_ns() // 1_000_000) / 1000.0
        if remaining_seconds <= 0:
            raise TimeoutError("provider corrector deadline expired")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if endpoint.api_key:
            headers["Authorization"] = f"Bearer {endpoint.api_key}"
        payload = {
            "model": raw_model,
            "messages": [
                {"role": "system", "content": corrector_system_message()},
                {
                    "role": "user",
                    "content": (
                        f"Language: {request.language or 'auto'}.\nOriginal transcript:\n{request.original_text}"
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": self._max_output_tokens,
            "stream": False,
        }
        session, owned_session = self._request_session()
        try:
            response = session.post(
                _chat_completions_url(endpoint),
                json=payload,
                headers=headers,
                timeout=(min(3.0, remaining_seconds), remaining_seconds),
                allow_redirects=False,
                stream=True,
            )
            if 300 <= int(response.status_code) < 400:
                raise RuntimeError("provider corrector redirect is forbidden")
            response.raise_for_status()
            body = _bounded_response_bytes(response, maximum=self._response_max_bytes)
        finally:
            if owned_session:
                session.close()
        try:
            envelope = json.loads(body)
        except (TypeError, ValueError) as exc:
            raise ValueError("provider corrector response is not JSON") from exc
        content = _openai_message_content(envelope)
        corrected = parse_corrector_output(content)
        self._refresh_catalog_if_needed()
        revision = self._model_revisions.get(request.model_id, "runtime-unpinned")
        return GenerativeCorrectorEngineResult(
            corrected_text=corrected,
            model_id=request.model_id,
            model_revision=revision,
            engine_id=f"{provider_id}-http",
        )

    def _refresh_catalog_if_needed(self) -> None:
        now = time.monotonic()
        if self._catalog_checked_at and now - self._catalog_checked_at <= self._discovery_ttl:
            return
        if self._catalog_checked_at:
            self._schedule_catalog_refresh()
            return
        with self._catalog_lock:
            now = time.monotonic()
            if self._catalog_checked_at and now - self._catalog_checked_at <= self._discovery_ttl:
                return
            (
                self._model_ids,
                self._model_revisions,
                self._ready_provider_ids,
            ) = self._load_catalog()
            self._catalog_checked_at = now

    def _schedule_catalog_refresh(self) -> None:
        with self._catalog_lock:
            now = time.monotonic()
            if self._catalog_refreshing or now - self._catalog_checked_at <= self._discovery_ttl:
                return
            self._catalog_refreshing = True
        threading.Thread(
            target=self._refresh_catalog_in_background,
            name="voice-corrector-model-catalog-refresh",
            daemon=True,
        ).start()

    def _refresh_catalog_in_background(self) -> None:
        try:
            model_ids, revisions, ready_provider_ids = self._load_catalog()
        except Exception:
            model_ids = ()
            revisions = {}
            ready_provider_ids = ()
        with self._catalog_lock:
            # Discovery is the readiness proof for external providers. Keeping
            # the previous catalog after a failed refresh would advertise an
            # offline endpoint indefinitely, so every completed refresh
            # replaces the full snapshot, including a failed/empty one.
            self._model_ids = model_ids
            self._model_revisions = revisions
            self._ready_provider_ids = ready_provider_ids
            self._catalog_checked_at = time.monotonic()
            self._catalog_refreshing = False

    def _load_catalog(self) -> tuple[tuple[str, ...], dict[str, str], tuple[str, ...]]:
        ids: list[str] = []
        revisions: dict[str, str] = {}
        ready_provider_ids: list[str] = []
        for endpoint in self._endpoints.values():
            discovered = self._discover(endpoint)
            if discovered is None:
                continue
            ready_provider_ids.append(endpoint.provider_id)
            for model_id, revision in discovered:
                if len(ids) >= 64:
                    break
                qualified = f"{endpoint.provider_id}:{model_id}"
                if not _QUALIFIED_MODEL_RE.fullmatch(qualified) or qualified in ids:
                    continue
                ids.append(qualified)
                revisions[qualified] = revision
        return tuple(ids), revisions, tuple(sorted(ready_provider_ids))

    def _discover(self, endpoint: CorrectorProviderEndpoint) -> list[tuple[str, str]] | None:
        headers = {"Accept": "application/json"}
        if endpoint.api_key:
            headers["Authorization"] = f"Bearer {endpoint.api_key}"
        url = f"{endpoint.base_url}/api/tags" if endpoint.provider_id == "ollama" else f"{endpoint.base_url}/models"
        session, owned_session = self._request_session()
        try:
            response = session.get(
                url,
                headers=headers,
                timeout=(self._discovery_timeout, self._discovery_timeout),
                allow_redirects=False,
                stream=True,
            )
            if 300 <= int(response.status_code) < 400:
                return None
            response.raise_for_status()
            payload = json.loads(_bounded_response_bytes(response, maximum=self._response_max_bytes))
        except (requests.RequestException, OSError, TypeError, ValueError):
            return None
        finally:
            if owned_session:
                session.close()
        if not isinstance(payload, Mapping):
            return None
        raw_models = payload.get("models") if endpoint.provider_id == "ollama" else payload.get("data")
        if not isinstance(raw_models, list):
            return None
        discovered: list[tuple[str, str]] = []
        for item in raw_models:
            if not isinstance(item, Mapping):
                continue
            model_id = str(item.get("name") or item.get("model") or item.get("id") or "").strip()
            if not model_id:
                continue
            digest = str(item.get("digest") or "").removeprefix("sha256:").strip().lower()
            revision = f"sha256-{digest}" if re.fullmatch(r"[0-9a-f]{8,64}", digest) else "runtime-unpinned"
            discovered.append((model_id, revision))
        return discovered

    def _request_session(self) -> tuple[requests.Session, bool]:
        if self._injected_session is not None:
            return self._injected_session, False
        session = requests.Session()
        session.trust_env = False
        return session, True


class CompositeGenerativeCorrectorEngine:
    """Select one execution engine locally; it never delegates to another worker."""

    def __init__(self, engines: Sequence[GenerativeCorrectorEngine]) -> None:
        self._engines = tuple(engines)
        if not self._engines:
            raise ValueError("composite corrector requires at least one engine")

    @property
    def engine_id(self) -> str:
        return "composite-corrector"

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(model for engine in self._engines for model in engine.model_ids))[:64]

    @property
    def provider_ids(self) -> tuple[str, ...]:
        values: list[str] = []
        for engine in self._engines:
            values.extend(str(item) for item in getattr(engine, "provider_ids", ()))
        return tuple(dict.fromkeys(values))

    @property
    def ready_provider_ids(self) -> tuple[str, ...]:
        values: list[str] = []
        for engine in self._engines:
            values.extend(str(item) for item in getattr(engine, "ready_provider_ids", ()))
        return tuple(dict.fromkeys(values))

    def health_snapshot(self) -> dict[str, tuple[str, ...]]:
        """Combine each engine's internally consistent health view."""

        model_ids: list[str] = []
        provider_ids: list[str] = []
        ready_provider_ids: list[str] = []
        for engine in self._engines:
            snapshot_reader = getattr(engine, "health_snapshot", None)
            if callable(snapshot_reader):
                snapshot = snapshot_reader()
                model_ids.extend(str(item) for item in snapshot.get("model_ids", ()))
                provider_ids.extend(str(item) for item in snapshot.get("provider_ids", ()))
                ready_provider_ids.extend(str(item) for item in snapshot.get("ready_provider_ids", ()))
                continue
            model_ids.extend(str(item) for item in engine.model_ids)
            provider_ids.extend(str(item) for item in getattr(engine, "provider_ids", ()))
            ready_provider_ids.extend(str(item) for item in getattr(engine, "ready_provider_ids", ()))
        return {
            "model_ids": tuple(dict.fromkeys(model_ids))[:64],
            "provider_ids": tuple(dict.fromkeys(provider_ids)),
            "ready_provider_ids": tuple(dict.fromkeys(ready_provider_ids)),
        }

    def supports_model(self, model_id: str) -> bool:
        return any(_supports_model(engine, model_id) for engine in self._engines)

    def correct(self, request: VoiceCorrectorWorkerRequest) -> GenerativeCorrectorEngineResult:
        for engine in self._engines:
            if _supports_model(engine, request.model_id):
                return engine.correct(request)
        raise LookupError("requested corrector model is not allowlisted")


def _supports_model(engine: GenerativeCorrectorEngine, model_id: str) -> bool:
    supports = getattr(engine, "supports_model", None)
    return bool(supports(model_id)) if callable(supports) else model_id in engine.model_ids


def _split_model_id(value: str) -> tuple[str, str]:
    normalized = str(value or "").strip()
    if not _QUALIFIED_MODEL_RE.fullmatch(normalized) or ":" not in normalized:
        raise ValueError("provider corrector model identifier is invalid")
    provider_id, raw_model = normalized.split(":", 1)
    if not _PROVIDER_RE.fullmatch(provider_id) or not raw_model:
        raise ValueError("provider corrector model identifier is invalid")
    return provider_id, raw_model


def _normalize_base_url(provider_id: str, value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/api/generate", "/api/chat", "/api/tags", "/models"):
        if raw.casefold().endswith(suffix):
            raw = raw[: -len(suffix)].rstrip("/")
            break
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("corrector provider endpoint is invalid")
    path = parsed.path.rstrip("/")
    if provider_id == "ollama" and path.casefold().endswith("/v1"):
        # Ollama exposes two API surfaces below the same deployment root:
        # `/api/*` for native discovery and `/v1/*` for OpenAI-compatible
        # inference. Strip only that terminal API marker, never a reverse-
        # proxy prefix such as `/tenant/local-llm`.
        path = path[: -len("/v1")].rstrip("/")
    elif provider_id != "ollama" and not path.casefold().endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _chat_completions_url(endpoint: CorrectorProviderEndpoint) -> str:
    base = f"{endpoint.base_url}/v1" if endpoint.provider_id == "ollama" else endpoint.base_url
    return f"{base}/chat/completions"


def _bounded_response_bytes(response: Any, *, maximum: int) -> bytes:
    content_length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
    try:
        try:
            declared_length = int(content_length) if content_length else None
        except (TypeError, ValueError) as exc:
            raise ValueError("provider corrector response content length is invalid") from exc
        if declared_length is not None and declared_length > maximum:
            raise ValueError("provider corrector response exceeds its byte limit")
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > maximum:
                raise ValueError("provider corrector response exceeds its byte limit")
            chunks.append(bytes(chunk))
        return b"".join(chunks)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _openai_message_content(payload: object) -> str:
    if not isinstance(payload, Mapping):
        raise ValueError("provider corrector response envelope is invalid")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ValueError("provider corrector response choices are invalid")
    message = choices[0].get("message")
    if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
        raise ValueError("provider corrector response content is invalid")
    return str(message["content"]).strip()
