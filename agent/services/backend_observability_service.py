from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import Any

from agent.services.prompt_trace_service import get_prompt_trace_service
from agent.services.repository_registry import get_repository_registry


class BackendObservabilityService:
    """Read-model service for backend-level execution observability."""

    @staticmethod
    def _safe_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _classify_backend(self, task: Any) -> str:
        verification_status = self._safe_dict(getattr(task, "verification_status", None))
        execution_routing = self._safe_dict(verification_status.get("execution_routing"))
        backend = str(execution_routing.get("backend") or "").strip().lower()
        if backend:
            return backend

        last_proposal = self._safe_dict(getattr(task, "last_proposal", None))
        backend = str(last_proposal.get("backend") or "").strip().lower()
        if backend:
            return backend

        worker_url = str(getattr(task, "assigned_agent_url", "") or "").strip().lower()
        if "opencode" in worker_url:
            return "opencode"
        if "hermes" in worker_url:
            return "hermes"
        if "ananta" in worker_url:
            return "ananta-worker"
        if "sgpt" in worker_url:
            return "sgpt"
        return "unknown"

    def summary(self, *, lookback_seconds: int = 3600, trace_limit: int = 800) -> dict[str, Any]:
        now_ts = time.time()
        lookback = max(60, min(int(lookback_seconds or 3600), 86400))
        since_ts = now_ts - lookback

        repos = get_repository_registry()
        tasks = list(repos.task_repo.get_all() or [])
        recent_tasks = [
            task
            for task in tasks
            if float(getattr(task, "updated_at", 0.0) or 0.0) >= since_ts
        ]

        by_backend: dict[str, dict[str, Any]] = {}
        for task in recent_tasks:
            backend = self._classify_backend(task)
            task_id = str(getattr(task, "id", "") or "")
            status = str(getattr(task, "status", "") or "unknown").strip().lower()
            verification_status = self._safe_dict(getattr(task, "verification_status", None))
            strategy = self._safe_dict(verification_status.get("autopilot_strategy"))
            reason_code = str(strategy.get("reason_code") or getattr(task, "status_reason_code", "") or "").strip() or None
            failures = list(strategy.get("last_failures") or [])

            bucket = by_backend.setdefault(
                backend,
                {
                    "task_count": 0,
                    "status_counts": Counter(),
                    "reason_codes": Counter(),
                    "failure_types": Counter(),
                    "recent_task_ids": [],
                },
            )
            bucket["task_count"] += 1
            bucket["status_counts"][status] += 1
            if reason_code:
                bucket["reason_codes"][reason_code] += 1
            for failure in failures:
                if not isinstance(failure, dict):
                    continue
                failure_type = str(failure.get("failure_type") or "").strip()
                if failure_type:
                    bucket["failure_types"][failure_type] += 1
            if task_id and len(bucket["recent_task_ids"]) < 20:
                bucket["recent_task_ids"].append(task_id)

        trace_service = get_prompt_trace_service()
        traces = list(trace_service.list_traces(limit=max(20, min(int(trace_limit), 2000)), since=since_ts) or [])
        trace_counts_by_provider = Counter()
        trace_success_by_provider = Counter()
        trace_model_counts: dict[str, Counter] = defaultdict(Counter)
        for trace in traces:
            provider = str(getattr(trace, "provider", "") or "unknown").strip().lower()
            model = str(getattr(trace, "model", "") or "unknown").strip()
            trace_counts_by_provider[provider] += 1
            if getattr(trace, "success", None) is True:
                trace_success_by_provider[provider] += 1
            trace_model_counts[provider][model] += 1

        normalized_backends: dict[str, Any] = {}
        for backend, bucket in by_backend.items():
            task_count = max(1, int(bucket["task_count"]))
            normalized_backends[backend] = {
                "task_count": int(bucket["task_count"]),
                "status_counts": dict(bucket["status_counts"]),
                "status_ratios": {
                    key: round(float(val) / float(task_count), 4)
                    for key, val in dict(bucket["status_counts"]).items()
                },
                "reason_codes": dict(bucket["reason_codes"]),
                "failure_types": dict(bucket["failure_types"]),
                "recent_task_ids": list(bucket["recent_task_ids"]),
            }

        return {
            "observed_at": now_ts,
            "lookback_seconds": lookback,
            "task_sample_size": len(recent_tasks),
            "trace_sample_size": len(traces),
            "backends": normalized_backends,
            "llm_trace_by_provider": {
                provider: {
                    "count": int(trace_counts_by_provider.get(provider, 0)),
                    "success_count": int(trace_success_by_provider.get(provider, 0)),
                    "success_ratio": round(
                        float(trace_success_by_provider.get(provider, 0))
                        / float(max(1, trace_counts_by_provider.get(provider, 0))),
                        4,
                    ),
                    "models": dict(trace_model_counts.get(provider, {})),
                }
                for provider in sorted(trace_counts_by_provider.keys())
            },
        }


_SERVICE = BackendObservabilityService()


def get_backend_observability_service() -> BackendObservabilityService:
    return _SERVICE

