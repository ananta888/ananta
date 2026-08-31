"""Hub-owned task admission for local runtime capability refreshes."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from agent.services.task_queue_service import get_task_queue_service


class LocalRuntimeCapabilityRefreshDispatcher:
    """Coalesce requests and persist one bounded worker task in the Hub queue."""

    _PROVIDERS = frozenset({"ollama", "lmstudio"})

    def __init__(
        self,
        *,
        provider_urls: Mapping[str, object],
        queue: Any | None = None,
        coalesce_seconds: int = 30,
        clock=time.time,
    ) -> None:
        self._provider_urls = dict(provider_urls)
        self._queue = queue or get_task_queue_service()
        self._coalesce_seconds = max(1, min(int(coalesce_seconds), 300))
        self._clock = clock
        self._lock = threading.Lock()
        self._recent: dict[str, tuple[float, str]] = {}

    def dispatch(self, *, provider_id: str | None, requested_by: str) -> str:
        provider = str(provider_id or "all").strip().lower()
        if provider != "all" and provider not in self._PROVIDERS:
            raise ValueError("local_runtime_provider_unknown")
        now = float(self._clock())
        with self._lock:
            recent = self._recent.get(provider)
            if recent is not None and now - recent[0] < self._coalesce_seconds:
                return recent[1]
            targets = self._targets(provider)
            if not targets:
                raise ValueError("local_runtime_provider_unconfigured")
            bucket = int(now // self._coalesce_seconds)
            identity = hashlib.sha256(f"{provider}\0{bucket}".encode()).hexdigest()[:32]
            task_id = f"local-runtime-refresh-{identity}"
            actor_digest = hashlib.sha256(str(requested_by or "unknown").encode()).hexdigest()
            self._queue.ingest_task(
                task_id=task_id,
                status="created",
                title="Refresh local runtime capability snapshots",
                description="Read provider metadata and return bounded capability snapshots to the Hub.",
                priority="high",
                created_by="system:local-runtime-capabilities",
                source="system",
                tags=["local-runtime", "capability-refresh", "read-only"],
                event_type="local_runtime_capability_refresh_queued",
                event_details={"provider_id": provider, "requested_by_sha256": actor_digest},
                extra_fields={
                    "task_kind": "local_runtime_capability_refresh",
                    "required_capabilities": ["local_runtime_capability_discovery"],
                    "worker_execution_context": {
                        "schema": "ananta.local-runtime-capability-refresh-task.v1",
                        "local_runtime_capability_refresh": {
                            "targets": targets,
                        },
                    },
                },
            )
            self._recent[provider] = (now, task_id)
            return task_id

    def _targets(self, provider: str) -> list[dict[str, str]]:
        selected = sorted(self._PROVIDERS if provider == "all" else {provider})
        targets: list[dict[str, str]] = []
        for provider_id in selected:
            configured = str(self._provider_urls.get(provider_id) or "").strip()
            root = self._origin_root(configured)
            if root:
                targets.append(
                    {
                        "provider_id": provider_id,
                        "base_url": root,
                        "runtime_version": "unknown",
                    }
                )
        return targets

    @staticmethod
    def _origin_root(value: str) -> str | None:
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                return None
            return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
        except ValueError:
            return None


__all__ = ["LocalRuntimeCapabilityRefreshDispatcher"]
