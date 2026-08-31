"""Worker execution adapter for delegated local runtime metadata discovery."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agent.services.local_runtime_capability_cache import LocalRuntimeCapabilityCache
from agent.services.local_runtime_capability_discovery import (
    LmStudioCapabilityDiscoveryAdapter,
    LocalRuntimeRefreshCoordinator,
    OllamaCapabilityDiscoveryAdapter,
)
from agent.services.local_runtime_http_client import (
    LocalRuntimeEndpointPolicy,
    LocalRuntimeHttpClient,
)


class LocalRuntimeCapabilityRefreshHandler:
    """Perform only the read-only probe assigned by the Hub task."""

    def __init__(
        self,
        *,
        cache_path: str | Path | None = None,
        client_factory: Callable[[LocalRuntimeEndpointPolicy], Any] = LocalRuntimeHttpClient,
    ) -> None:
        self._cache_path = Path(
            cache_path
            or os.environ.get(
                "ANANTA_LOCAL_RUNTIME_WORKER_CACHE",
                "data/local-runtime-capabilities-worker.json",
            )
        )
        self._client_factory = client_factory

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "executable",
            "proposal_status": "executable",
            "selected_strategy": "deterministic_handler",
            "reason": "local_runtime_capability_refresh_ready",
            "safety_flags": {"read_only": True, "mutates_runtime": False},
        }

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        task = kwargs.get("task") or {}
        targets = self._targets(task)
        origins = frozenset(self._origin(item["base_url"]) for item in targets)
        policy = LocalRuntimeEndpointPolicy(
            origins,
            allow_loopback=True,
            allow_private=self._flag("ANANTA_LOCAL_RUNTIME_ALLOW_PRIVATE"),
        )
        client = self._client_factory(policy)
        adapters = []
        for target in targets:
            adapter_type = (
                OllamaCapabilityDiscoveryAdapter
                if target["provider_id"] == "ollama"
                else LmStudioCapabilityDiscoveryAdapter
            )
            adapters.append(
                adapter_type(
                    client=client,
                    base_url=target["base_url"],
                    runtime_version=target["runtime_version"],
                )
            )
        coordinator = LocalRuntimeRefreshCoordinator(
            adapters,
            LocalRuntimeCapabilityCache(self._cache_path),
        )
        results = coordinator.refresh_all()
        return {
            "schema": "ananta.local-runtime-capability-refresh-result.v1",
            "status": "completed" if all(item.status != "failed" for item in results) else "degraded",
            "providers": [item.to_wire() for item in results],
            "exit_code": 0 if any(item.status in {"healthy", "stale"} for item in results) else 1,
        }

    @staticmethod
    def _targets(task: Mapping[str, Any]) -> list[dict[str, str]]:
        context = task.get("worker_execution_context")
        refresh = context.get("local_runtime_capability_refresh") if isinstance(context, Mapping) else None
        raw = refresh.get("targets") if isinstance(refresh, Mapping) else None
        if not isinstance(raw, list) or not 1 <= len(raw) <= 2:
            raise ValueError("local_runtime_refresh_targets_invalid")
        result: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, Mapping) or set(item) != {"provider_id", "base_url", "runtime_version"}:
                raise ValueError("local_runtime_refresh_target_invalid")
            provider = str(item.get("provider_id") or "")
            base_url = str(item.get("base_url") or "")
            version = str(item.get("runtime_version") or "")
            if provider not in {"ollama", "lmstudio"} or provider in seen or not base_url or not version:
                raise ValueError("local_runtime_refresh_target_invalid")
            seen.add(provider)
            result.append({"provider_id": provider, "base_url": base_url, "runtime_version": version})
        return result

    @staticmethod
    def _origin(value: str) -> str:
        parsed = urlsplit(value)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("local_runtime_refresh_target_invalid")
        return f"{parsed.scheme}://{parsed.hostname.lower()}:{port}"

    @staticmethod
    def _flag(name: str) -> bool:
        return str(os.environ.get(name, "0")).strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["LocalRuntimeCapabilityRefreshHandler"]
