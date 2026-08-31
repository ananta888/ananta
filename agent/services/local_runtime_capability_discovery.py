"""Provider adapters and Hub-owned refresh coordination for local runtimes."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.local_runtime_capability_cache import LocalRuntimeCapabilityCache
from agent.services.local_runtime_capability_contracts import RuntimeModelSnapshot
from agent.services.local_runtime_capability_normalizer import LocalRuntimeCapabilityNormalizer
from agent.services.local_runtime_http_client import LocalRuntimeHttpClient, LocalRuntimeTransportError


class LocalRuntimeDiscoveryPort(Protocol):
    provider_id: str

    def discover(self) -> Sequence[RuntimeModelSnapshot]: ...


@dataclass(frozen=True, slots=True)
class RuntimeRefreshResult:
    provider_id: str
    snapshots: tuple[RuntimeModelSnapshot, ...]
    status: str
    reason_code: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "model_count": len(self.snapshots),
            "snapshots": [item.to_dict() for item in self.snapshots],
        }


class OllamaCapabilityDiscoveryAdapter:
    provider_id = "ollama"

    def __init__(
        self,
        *,
        client: LocalRuntimeHttpClient,
        base_url: str,
        runtime_version: str,
        timeout_seconds: float = 5.0,
        maximum_models: int = 64,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._runtime_version = runtime_version
        self._timeout = max(0.1, min(float(timeout_seconds), 60.0))
        self._maximum_models = max(1, min(int(maximum_models), 256))
        self._normalizer = LocalRuntimeCapabilityNormalizer()

    def discover(self) -> Sequence[RuntimeModelSnapshot]:
        catalog = self._client.request_json("GET", self._base_url, "/api/tags", timeout_seconds=self._timeout)
        rows = catalog.get("models")
        candidates = (
            [dict(item) for item in rows[: self._maximum_models] if isinstance(item, Mapping)]
            if isinstance(rows, list)
            else []
        )
        candidates = [item for item in candidates if str(item.get("name") or item.get("model") or "").strip()]
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for candidate in candidates:
            key = (
                str(candidate.get("name") or candidate.get("model")),
                str(candidate.get("digest") or ""),
            )
            unique.setdefault(key, candidate)
        candidates = list(unique.values())
        snapshots: list[RuntimeModelSnapshot] = []
        with ThreadPoolExecutor(max_workers=min(4, len(candidates) or 1)) as executor:
            futures = {
                executor.submit(self._detail, candidate): candidate
                for candidate in candidates
            }
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    detail = future.result()
                except LocalRuntimeTransportError:
                    detail = candidate
                model_id = str(candidate.get("name") or candidate.get("model"))
                merged = {**candidate, **dict(detail)}
                snapshots.append(self._normalizer.normalize(
                    provider_id=self.provider_id,
                    model_id=model_id,
                    runtime_version=self._runtime_version,
                    model_digest=str(candidate.get("digest") or ""),
                    metadata=merged,
                ))
        return tuple(sorted(snapshots, key=lambda item: item.model_id))

    def _detail(self, candidate: Mapping[str, Any]) -> Mapping[str, Any]:
        model_id = str(candidate.get("name") or candidate.get("model"))
        return self._client.request_json(
            "POST", self._base_url, "/api/show", timeout_seconds=self._timeout, payload={"model": model_id}
        )


class LmStudioCapabilityDiscoveryAdapter:
    provider_id = "lmstudio"

    def __init__(
        self,
        *,
        client: LocalRuntimeHttpClient,
        base_url: str,
        runtime_version: str,
        timeout_seconds: float = 5.0,
        maximum_models: int = 64,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._runtime_version = runtime_version
        self._timeout = max(0.1, min(float(timeout_seconds), 60.0))
        self._maximum_models = max(1, min(int(maximum_models), 256))
        self._normalizer = LocalRuntimeCapabilityNormalizer()

    def discover(self) -> Sequence[RuntimeModelSnapshot]:
        compatible = self._client.request_json("GET", self._base_url, "/v1/models", timeout_seconds=self._timeout)
        native_by_id: dict[str, Mapping[str, Any]] = {}
        try:
            native = self._client.request_json("GET", self._base_url, "/api/v1/models", timeout_seconds=self._timeout)
            native_rows = native.get("models") or native.get("data")
            if isinstance(native_rows, list):
                native_by_id = {
                    str(item.get("id") or item.get("key") or ""): item
                    for item in native_rows[: self._maximum_models]
                    if isinstance(item, Mapping) and str(item.get("id") or item.get("key") or "")
                }
        except LocalRuntimeTransportError:
            pass
        rows = compatible.get("data")
        result: list[RuntimeModelSnapshot] = []
        if isinstance(rows, list):
            for item in rows[: self._maximum_models]:
                if not isinstance(item, Mapping):
                    continue
                model_id = str(item.get("id") or "").strip()
                if not model_id:
                    continue
                metadata = {**dict(item), **dict(native_by_id.get(model_id) or {})}
                result.append(self._normalizer.normalize(
                    provider_id=self.provider_id,
                    model_id=model_id,
                    runtime_version=self._runtime_version,
                    model_digest=str(metadata.get("digest") or metadata.get("sha256") or ""),
                    metadata=metadata,
                ))
        return tuple(sorted(result, key=lambda item: item.model_id))


class LocalRuntimeRefreshCoordinator:
    """Coalesce refreshes in the Hub; adapters perform discovery only."""

    def __init__(self, adapters: Sequence[LocalRuntimeDiscoveryPort], cache: LocalRuntimeCapabilityCache) -> None:
        self._adapters = {adapter.provider_id: adapter for adapter in adapters}
        self._cache = cache
        self._locks = {provider_id: threading.Lock() for provider_id in self._adapters}

    def refresh(self, provider_id: str) -> RuntimeRefreshResult:
        provider = str(provider_id or "").strip().lower()
        adapter = self._adapters.get(provider)
        if adapter is None:
            return RuntimeRefreshResult(provider, (), "failed", "local_runtime_provider_unknown")
        with self._locks[provider]:
            try:
                snapshots = tuple(adapter.discover())
                self._cache.replace_provider(provider, snapshots)
                return RuntimeRefreshResult(provider, snapshots, "healthy")
            except Exception as exc:
                reason = str(exc) if isinstance(exc, LocalRuntimeTransportError) else "local_runtime_discovery_failed"
                cached = tuple(item for item in self._cache.load() if item.provider_id == provider)
                stale = tuple(self._stale(item) for item in cached)
                return RuntimeRefreshResult(provider, stale, "stale" if stale else "failed", reason)

    def refresh_all(self) -> tuple[RuntimeRefreshResult, ...]:
        with ThreadPoolExecutor(max_workers=max(1, min(4, len(self._adapters)))) as executor:
            results = tuple(executor.map(self.refresh, sorted(self._adapters)))
        return results

    @staticmethod
    def _stale(item: RuntimeModelSnapshot) -> RuntimeModelSnapshot:
        raw = item.to_dict(include_snapshot_digest=False)
        raw["stale"] = True
        return RuntimeModelSnapshot.from_mapping(raw)


__all__ = [
    "LmStudioCapabilityDiscoveryAdapter",
    "LocalRuntimeDiscoveryPort",
    "LocalRuntimeRefreshCoordinator",
    "OllamaCapabilityDiscoveryAdapter",
    "RuntimeRefreshResult",
]
