from __future__ import annotations

from collections import defaultdict
from typing import Any

from agent.common.errors import PermanentError
from agent.services.repository_registry import get_repository_registry


class CostAggregationError(PermanentError):
    def __init__(self, message: str = "cost_aggregation_failed", details: dict | None = None):
        super().__init__(message, details, status_code=422)


class PricingConfigError(PermanentError):
    def __init__(self, message: str = "pricing_config_invalid", details: dict | None = None):
        super().__init__(message, details, status_code=500)


class CostAggregationService:
    def _task_value(self, task: Any, key: str, default: Any = None) -> Any:
        if isinstance(task, dict):
            return task.get(key, default)
        return getattr(task, key, default)

    def _normalize_cost_summary(self, summary: dict[str, Any], *, task: Any, source: str) -> dict[str, Any]:
        if not isinstance(summary, dict):
            raise CostAggregationError(
                details={"task_id": self._task_value(task, "id"), "summary_type": type(summary).__name__, "source": source}
            )
        provider = str(summary.get("provider") or "").strip() or None
        model = str(summary.get("model") or "").strip() or None
        task_kind = str(
            summary.get("task_kind") or self._task_value(task, "task_kind") or self._task_value(task, "status") or ""
        ).strip() or None
        return {
            "task_id": self._task_value(task, "id"),
            "title": self._task_value(task, "title"),
            "status": self._task_value(task, "status"),
            "task_kind": task_kind,
            "provider": provider,
            "model": model,
            "tokens_total": max(0, int(summary.get("tokens_total") or 0)),
            "cost_units": round(max(0.0, float(summary.get("cost_units") or 0.0)), 6),
            "latency_ms": max(0, int(summary.get("latency_ms") or 0)) if summary.get("latency_ms") is not None else None,
            "pricing_source": str(summary.get("pricing_source") or "").strip() or None,
            "source": source,
            "available": True,
        }

    def task_cost_summary(self, task: Any) -> dict[str, Any]:
        history = list(self._task_value(task, "history", []) or [])
        for event in reversed(history):
            if not isinstance(event, dict) or event.get("event_type") != "execution_result":
                continue
            summary = event.get("cost_summary")
            if isinstance(summary, dict):
                return self._normalize_cost_summary(summary, task=task, source="task_history")

        verification_status = self._task_value(task, "verification_status", {}) or {}
        verification_results = verification_status.get("results") if isinstance(verification_status, dict) else {}
        execution_cost = (verification_results or {}).get("execution_cost") if isinstance(verification_results, dict) else None
        if isinstance(execution_cost, dict):
            return self._normalize_cost_summary(execution_cost, task=task, source="verification_status")

        return {
            "task_id": self._task_value(task, "id"),
            "title": self._task_value(task, "title"),
            "status": self._task_value(task, "status"),
            "task_kind": self._task_value(task, "task_kind"),
            "provider": None,
            "model": None,
            "tokens_total": 0,
            "cost_units": 0.0,
            "latency_ms": None,
            "pricing_source": None,
            "source": "unavailable",
            "available": False,
        }

    def aggregate_tasks(self, tasks: list[Any]) -> dict[str, Any]:
        task_items = [self.task_cost_summary(task) for task in list(tasks or [])]
        provider_breakdown: dict[str, dict[str, Any]] = {}
        task_kind_breakdown: dict[str, dict[str, Any]] = defaultdict(lambda: {"task_kind": None, "task_count": 0, "cost_units": 0.0, "tokens_total": 0})
        total_cost_units = 0.0
        total_tokens = 0
        total_latency_ms = 0
        tasks_with_cost = 0

        for item in task_items:
            if item.get("available"):
                tasks_with_cost += 1
            total_cost_units += float(item.get("cost_units") or 0.0)
            total_tokens += int(item.get("tokens_total") or 0)
            total_latency_ms += int(item.get("latency_ms") or 0)

            task_kind = str(item.get("task_kind") or "").strip() or "unknown"
            kind_row = task_kind_breakdown[task_kind]
            kind_row["task_kind"] = task_kind
            kind_row["task_count"] += 1
            kind_row["cost_units"] = round(float(kind_row["cost_units"]) + float(item.get("cost_units") or 0.0), 6)
            kind_row["tokens_total"] += int(item.get("tokens_total") or 0)

            provider = str(item.get("provider") or "").strip()
            model = str(item.get("model") or "").strip()
            if provider or model:
                key = f"{provider}:{model}"
                row = provider_breakdown.setdefault(
                    key,
                    {"provider": provider or None, "model": model or None, "task_count": 0, "cost_units": 0.0, "tokens_total": 0},
                )
                row["task_count"] += 1
                row["cost_units"] = round(float(row["cost_units"]) + float(item.get("cost_units") or 0.0), 6)
                row["tokens_total"] += int(item.get("tokens_total") or 0)

        return {
            "task_count": len(task_items),
            "tasks_with_cost": tasks_with_cost,
            "tasks_without_cost": max(0, len(task_items) - tasks_with_cost),
            "total_cost_units": round(total_cost_units, 6),
            "total_tokens": total_tokens,
            "total_latency_ms": total_latency_ms,
            "currency": "cost_units",
            "items": task_items,
            "task_kind_breakdown": sorted(task_kind_breakdown.values(), key=lambda item: str(item.get("task_kind") or "")),
            "provider_breakdown": sorted(provider_breakdown.values(), key=lambda item: (str(item.get("provider") or ""), str(item.get("model") or ""))),
        }

    def aggregate_goal_costs(self, goal_id: str) -> dict[str, Any]:
        repos = get_repository_registry()
        tasks = repos.task_repo.get_by_goal_id(goal_id)
        aggregate = self.aggregate_tasks(tasks)
        aggregate["goal_id"] = goal_id
        aggregate["completed_tasks"] = len([task for task in tasks if self._task_value(task, "status") == "completed"])
        aggregate["failed_tasks"] = len([task for task in tasks if self._task_value(task, "status") == "failed"])
        return aggregate

    def attach_cost_to_verification_results(self, *, task: Any, results: dict[str, Any] | None) -> dict[str, Any]:
        merged = dict(results or {})
        merged["execution_cost"] = self.task_cost_summary(task)
        return merged


cost_aggregation_service = CostAggregationService()


def get_cost_aggregation_service() -> CostAggregationService:
    return cost_aggregation_service
