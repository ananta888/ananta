from __future__ import annotations

import threading
import time
from typing import Any

from agent.llm_integration import probe_lmstudio_runtime, probe_ollama_activity, probe_ollama_runtime


class ProviderObserverService:
    """Hub-side direct provider observer (independent from worker execution)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _cfg_float(cfg: dict[str, Any], key: str, default: float, min_v: float, max_v: float) -> float:
        try:
            value = float(cfg.get(key, default))
        except Exception:
            value = default
        return max(min_v, min(value, max_v))

    @staticmethod
    def _cfg_int(cfg: dict[str, Any], key: str, default: int, min_v: int, max_v: int) -> int:
        try:
            value = int(cfg.get(key, default))
        except Exception:
            value = default
        return max(min_v, min(value, max_v))

    def _probe_provider(self, provider: str, base_url: str, timeout_s: int, include_activity: bool) -> dict[str, Any]:
        started_at = time.time()
        error_detail: str | None = None
        try:
            if provider == "ollama":
                runtime = probe_ollama_runtime(base_url, timeout=timeout_s)
                activity = probe_ollama_activity(base_url, timeout=timeout_s) if include_activity else None
            elif provider == "lmstudio":
                runtime = probe_lmstudio_runtime(base_url, timeout=timeout_s)
                activity = None
            else:
                runtime = {"ok": False, "status": "unsupported_provider_probe"}
                activity = None
        except Exception as exc:
            runtime = {"ok": False, "status": "probe_exception"}
            activity = None
            error_detail = str(exc)[:200]
        ended_at = time.time()
        return {
            "provider": provider,
            "base_url": base_url,
            "ok": bool(runtime.get("ok")),
            "status": str(runtime.get("status") or "unknown"),
            "candidate_count": int(runtime.get("candidate_count") or 0),
            "runtime": runtime,
            "activity": activity if isinstance(activity, dict) else None,
            "started_at": started_at,
            "ended_at": ended_at,
            "latency_ms": max(0, int((ended_at - started_at) * 1000)),
            "source": "hub_direct_probe",
            **({"error_detail": error_detail} if error_detail else {}),
        }

    def snapshot(
        self,
        *,
        agent_config: dict[str, Any],
        provider_urls: dict[str, Any],
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        cfg = dict(agent_config or {})
        ttl_seconds = self._cfg_float(cfg, "provider_observer_ttl_seconds", 8.0, 1.0, 60.0)
        timeout_seconds = self._cfg_int(cfg, "provider_observer_timeout_seconds", 3, 1, 15)
        include_activity = bool(cfg.get("provider_observer_include_ollama_activity", True))
        enabled = bool(cfg.get("provider_observer_enabled", True))
        now = time.time()

        providers: list[tuple[str, str]] = []
        for name in ("ollama", "lmstudio"):
            url = str((provider_urls or {}).get(name) or "").strip()
            if url:
                providers.append((name, url))

        if not enabled:
            return {
                "enabled": False,
                "source": "hub_direct_probe",
                "providers": {},
                "observed_at": now,
                "ttl_seconds": ttl_seconds,
            }

        out: dict[str, Any] = {}
        for provider, base_url in providers:
            key = f"{provider}|{base_url}"
            with self._lock:
                cached = dict(self._cache.get(key) or {})
            observed_at = float(cached.get("observed_at") or 0.0)
            fresh = (now - observed_at) <= ttl_seconds and not force_refresh
            if fresh and cached:
                item = dict(cached)
                item["cache_hit"] = True
                out[provider] = item
                continue

            # Never hold cache lock while probing network endpoints.
            item = self._probe_provider(provider, base_url, timeout_seconds, include_activity=include_activity)
            item["observed_at"] = time.time()
            item["cache_hit"] = False
            with self._lock:
                self._cache[key] = dict(item)
            out[provider] = item

        return {
            "enabled": True,
            "source": "hub_direct_probe",
            "providers": out,
            "observed_at": time.time(),
            "ttl_seconds": ttl_seconds,
            "timeout_seconds": timeout_seconds,
        }


_provider_observer_service = ProviderObserverService()


def get_provider_observer_service() -> ProviderObserverService:
    return _provider_observer_service
