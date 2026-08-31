"""Hub-side acceptance of a delegated local runtime discovery result."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.local_runtime_capability_cache import LocalRuntimeCapabilityCache
from agent.services.local_runtime_capability_contracts import RuntimeModelSnapshot


class LocalRuntimeCapabilityResultIngestor:
    def __init__(self, cache: LocalRuntimeCapabilityCache) -> None:
        self._cache = cache

    def accept(self, *, task: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any] | None:
        if str(task.get("task_kind") or "").strip() != "local_runtime_capability_refresh":
            return None
        if response.get("schema") != "ananta.local-runtime-capability-refresh-result.v1":
            raise ValueError("local_runtime_refresh_result_schema_invalid")
        context = task.get("worker_execution_context")
        refresh = context.get("local_runtime_capability_refresh") if isinstance(context, Mapping) else None
        targets = refresh.get("targets") if isinstance(refresh, Mapping) else None
        allowed = {
            str(item.get("provider_id") or "")
            for item in (targets or ())
            if isinstance(item, Mapping)
        }
        providers = response.get("providers")
        if not isinstance(providers, list) or len(providers) > len(allowed):
            raise ValueError("local_runtime_refresh_result_invalid")
        accepted: list[RuntimeModelSnapshot] = []
        statuses: dict[str, str] = {}
        for provider_result in providers:
            if not isinstance(provider_result, Mapping):
                raise ValueError("local_runtime_refresh_result_invalid")
            provider_id = str(provider_result.get("provider_id") or "")
            if provider_id not in allowed or provider_id in statuses:
                raise ValueError("local_runtime_refresh_result_provider_invalid")
            status = str(provider_result.get("status") or "")
            if status not in {"healthy", "stale", "failed"}:
                raise ValueError("local_runtime_refresh_result_status_invalid")
            rows = provider_result.get("snapshots")
            if not isinstance(rows, list) or len(rows) > 512:
                raise ValueError("local_runtime_refresh_result_invalid")
            snapshots = [RuntimeModelSnapshot.from_mapping(item) for item in rows if isinstance(item, Mapping)]
            if len(snapshots) != len(rows) or any(item.provider_id != provider_id for item in snapshots):
                raise ValueError("local_runtime_refresh_result_binding_invalid")
            if status in {"healthy", "stale"}:
                self._cache.replace_provider(provider_id, snapshots)
            accepted.extend(snapshots)
            statuses[provider_id] = status
        return {
            "schema": "ananta.local-runtime-capability-refresh-acceptance.v1",
            "provider_statuses": statuses,
            "model_count": len(accepted),
        }


__all__ = ["LocalRuntimeCapabilityResultIngestor"]
