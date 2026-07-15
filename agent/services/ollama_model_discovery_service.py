"""Bounded Ollama model discovery for Hub-owned provider catalogs."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import requests

from agent.llm_integration_ollama import _normalize_ollama_base_url, _ollama_tags_url

OllamaProbe = Callable[[str, int], Mapping[str, Any]]
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_MODELS = 64


@dataclass(frozen=True)
class OllamaModelDiscovery:
    """Safe discovery result; configured models remain visible as offline fallback."""

    models: tuple[dict[str, Any], ...]
    available: bool
    status: str
    normalized_base_url: str | None
    used_configured_fallback: bool


class OllamaModelDiscoveryService:
    """Discover Ollama tags without coupling HTTP probing or caching to a route."""

    def __init__(
        self,
        *,
        probe: OllamaProbe | None = None,
        monotonic: Callable[[], float] | None = None,
        max_cache_entries: int = 128,
    ) -> None:
        self._probe = probe or _probe_ollama_catalog
        self._monotonic = monotonic or time.monotonic
        self._max_cache_entries = max(1, int(max_cache_entries))
        self._cache: dict[tuple[str, int, tuple[str, ...]], tuple[float, OllamaModelDiscovery]] = {}
        self._lock = threading.RLock()

    def discover(
        self,
        *,
        base_url: str | None,
        configured_models: Sequence[object] = (),
        timeout_seconds: int = 5,
        cache_ttl_seconds: int = 15,
        force_refresh: bool = False,
    ) -> OllamaModelDiscovery:
        normalized_base_url = _normalize_ollama_base_url(base_url)
        timeout = max(1, min(int(timeout_seconds), 60))
        ttl = max(0, min(int(cache_ttl_seconds), 3600))
        fallback_ids = _model_ids(configured_models)
        cache_key = (normalized_base_url or "", timeout, fallback_ids)
        now = self._monotonic()

        if not force_refresh and ttl > 0:
            with self._lock:
                cached = self._cache.get(cache_key)
                if cached is not None and now - cached[0] <= ttl:
                    return cached[1]

        result = self._probe_models(
            normalized_base_url=normalized_base_url,
            configured_models=fallback_ids,
            timeout_seconds=timeout,
        )
        if ttl > 0:
            with self._lock:
                self._prune_cache(now)
                self._cache[cache_key] = (now, result)
        return result

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def _probe_models(
        self,
        *,
        normalized_base_url: str | None,
        configured_models: tuple[str, ...],
        timeout_seconds: int,
    ) -> OllamaModelDiscovery:
        if not normalized_base_url:
            return _fallback_discovery(
                configured_models,
                status="invalid_url",
                normalized_base_url=None,
            )
        try:
            payload = self._probe(normalized_base_url, timeout_seconds)
        except Exception:
            return _fallback_discovery(
                configured_models,
                status="error",
                normalized_base_url=normalized_base_url,
            )

        if not isinstance(payload, Mapping):
            return _fallback_discovery(
                configured_models,
                status="invalid_response",
                normalized_base_url=normalized_base_url,
            )

        runtime_ids = _runtime_model_ids(payload.get("models"))
        if runtime_ids:
            available = bool(payload.get("ok"))
            return OllamaModelDiscovery(
                models=tuple(
                    {
                        "id": model_id,
                        "context_length": None,
                        "source": "ollama_api_tags",
                        "available": available,
                    }
                    for model_id in runtime_ids
                ),
                available=available,
                status=str(payload.get("status") or "ok"),
                normalized_base_url=normalized_base_url,
                used_configured_fallback=False,
            )
        return _fallback_discovery(
            configured_models,
            status=str(payload.get("status") or "reachable_no_models"),
            normalized_base_url=normalized_base_url,
        )

    def _prune_cache(self, now: float) -> None:
        expired_before = now - 3600.0
        for key, (created_at, _result) in list(self._cache.items()):
            if created_at < expired_before:
                self._cache.pop(key, None)
        overflow = len(self._cache) - self._max_cache_entries + 1
        if overflow <= 0:
            return
        oldest = sorted(self._cache.items(), key=lambda item: item[1][0])
        for key, _value in oldest[:overflow]:
            self._cache.pop(key, None)


def _fallback_discovery(
    configured_models: tuple[str, ...],
    *,
    status: str,
    normalized_base_url: str | None,
) -> OllamaModelDiscovery:
    return OllamaModelDiscovery(
        models=tuple(
            {
                "id": model_id,
                "context_length": None,
                "source": "configured_fallback",
                "available": False,
            }
            for model_id in configured_models
        ),
        available=False,
        status=status,
        normalized_base_url=normalized_base_url,
        used_configured_fallback=bool(configured_models),
    )


def _runtime_model_ids(raw_models: object) -> tuple[str, ...]:
    if not isinstance(raw_models, list):
        return ()
    return _model_ids(item.get("name") or item.get("id") for item in raw_models if isinstance(item, Mapping))


def _model_ids(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        model_id = str(raw or "").strip()
        if not model_id or len(model_id) > 512 or "\x00" in model_id or model_id in seen:
            continue
        seen.add(model_id)
        result.append(model_id)
        if len(result) >= _MAX_MODELS:
            break
    return tuple(result)


def _probe_ollama_catalog(base_url: str, timeout_seconds: int) -> Mapping[str, Any]:
    """Probe ``/api/tags`` without redirects, proxies, or unbounded reads."""

    tags_url = _ollama_tags_url(base_url)
    if not tags_url:
        return {"ok": False, "status": "invalid_url", "models": []}
    deadline = time.monotonic() + max(1, int(timeout_seconds))
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(
            tags_url,
            headers={"Accept": "application/json"},
            timeout=(min(3.0, float(timeout_seconds)), min(1.0, float(timeout_seconds))),
            allow_redirects=False,
            stream=True,
        )
        if 300 <= int(response.status_code) < 400:
            return {"ok": False, "status": "redirect_forbidden", "models": []}
        response.raise_for_status()
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            declared_length = int(content_length)
            if declared_length < 0:
                return {"ok": False, "status": "invalid_response", "models": []}
            if declared_length > _MAX_RESPONSE_BYTES:
                return {"ok": False, "status": "response_too_large", "models": []}
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if time.monotonic() > deadline:
                return {"ok": False, "status": "timeout", "models": []}
            if not chunk:
                continue
            size += len(chunk)
            if size > _MAX_RESPONSE_BYTES:
                return {"ok": False, "status": "response_too_large", "models": []}
            chunks.append(chunk)
        payload = json.loads(b"".join(chunks))
    except (requests.RequestException, OSError, TypeError, ValueError):
        return {"ok": False, "status": "error", "models": []}
    finally:
        session.close()
    raw_models = payload.get("models") if isinstance(payload, Mapping) else None
    candidates = raw_models if isinstance(raw_models, list) else []
    models = [
        item
        for item in candidates[:_MAX_MODELS]
        if isinstance(item, Mapping) and str(item.get("name") or item.get("id") or "").strip()
    ]
    return {
        "ok": True,
        "status": "ok" if models else "reachable_no_models",
        "models": models,
    }


ollama_model_discovery_service = OllamaModelDiscoveryService()


def get_ollama_model_discovery_service() -> OllamaModelDiscoveryService:
    return ollama_model_discovery_service


__all__ = [
    "OllamaModelDiscovery",
    "OllamaModelDiscoveryService",
    "get_ollama_model_discovery_service",
]
