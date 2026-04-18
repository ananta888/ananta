from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from agent.services.repository_registry import get_repository_registry


def _top(counter: Counter, *, limit: int = 8) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class OperationsObservabilityService:
    """Builds structured operational aggregates for dashboard and tuning views."""

    def build_dashboard_summary(self, *, tasks: list[dict], max_records: int = 300) -> dict[str, Any]:
        repos = get_repository_registry()
        recent_tasks = sorted(
            tasks,
            key=lambda task: float(task.get("updated_at") or task.get("created_at") or 0.0),
            reverse=True,
        )[: max(1, int(max_records))]
        task_by_id = {str(task.get("id")): task for task in recent_tasks if task.get("id")}

        verification_records = []
        for task_id in task_by_id:
            verification_records.extend(repos.verification_record_repo.get_by_task_id(task_id))
        policy_decisions = repos.policy_decision_repo.get_all(limit=max_records)

        task_kind_status: dict[str, Counter] = defaultdict(Counter)
        for task in recent_tasks:
            kind = str(task.get("task_kind") or "unknown").strip() or "unknown"
            status = str(task.get("status") or "unknown").strip().lower() or "unknown"
            task_kind_status[kind][status] += 1

        verification_failures = Counter()
        verification_by_kind: dict[str, Counter] = defaultdict(Counter)
        for record in verification_records:
            status = str(record.status or "unknown").strip().lower() or "unknown"
            task = task_by_id.get(record.task_id) or {}
            kind = str(task.get("task_kind") or "unknown").strip() or "unknown"
            verification_by_kind[kind][status] += 1
            if status not in {"passed", "ok", "completed"}:
                reason = record.escalation_code or record.escalation_reason or status
                verification_failures[str(reason or "unknown")] += 1

        routing_reasons = Counter()
        routing_by_kind: dict[str, Counter] = defaultdict(Counter)
        for decision in policy_decisions:
            if decision.task_id and decision.task_id not in task_by_id:
                continue
            task = task_by_id.get(decision.task_id or "") or {}
            kind = str(task.get("task_kind") or "unknown").strip() or "unknown"
            status = str(decision.status or "unknown").strip().lower() or "unknown"
            routing_by_kind[kind][status] += 1
            for reason in decision.reasons or [status]:
                routing_reasons[str(reason or "unknown")] += 1

        context_rows: list[dict[str, Any]] = []
        for task in recent_tasks:
            bundle_id = str(task.get("context_bundle_id") or "").strip()
            bundle = repos.context_bundle_repo.get_by_id(bundle_id) if bundle_id else None
            if bundle is None:
                continue
            metadata = dict(bundle.bundle_metadata or {})
            budget = dict(metadata.get("budget") or {})
            strategy = dict(metadata.get("strategy") or {})
            source_mix = dict(strategy.get("source_mix") or metadata.get("source_mix") or {})
            status = str(task.get("status") or "unknown").strip().lower() or "unknown"
            context_rows.append(
                {
                    "task_id": task.get("id"),
                    "task_kind": str(task.get("task_kind") or "unknown").strip() or "unknown",
                    "status": status,
                    "budget_utilization": min(1.0, max(0.0, _safe_float(budget.get("retrieval_utilization")))),
                    "chunk_count": len(bundle.chunks or []),
                    "token_estimate": int(bundle.token_estimate or 0),
                    "source_mix": source_mix,
                }
            )

        context_by_kind: dict[str, dict[str, Any]] = {}
        grouped_context: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in context_rows:
            grouped_context[str(row["task_kind"])].append(row)
        for kind, rows in grouped_context.items():
            count = max(1, len(rows))
            success_count = len([row for row in rows if row["status"] == "completed"])
            context_by_kind[kind] = {
                "count": len(rows),
                "success_rate": round(success_count / count, 4),
                "avg_budget_utilization": round(sum(float(row["budget_utilization"]) for row in rows) / count, 4),
                "avg_chunk_count": round(sum(int(row["chunk_count"]) for row in rows) / count, 2),
                "avg_token_estimate": round(sum(int(row["token_estimate"]) for row in rows) / count, 2),
            }

        return {
            "sample_size": len(recent_tasks),
            "root_causes": {
                "verification_failures": _top(verification_failures),
                "routing_reasons": _top(routing_reasons),
                "task_failures": _top(Counter(str(task.get("status_reason_code") or "unknown") for task in recent_tasks if str(task.get("status") or "").lower() in {"failed", "blocked"})),
            },
            "task_kind_outcomes": {
                kind: dict(counter)
                for kind, counter in sorted(task_kind_status.items(), key=lambda item: item[0])
            },
            "routing_by_task_kind": {
                kind: dict(counter)
                for kind, counter in sorted(routing_by_kind.items(), key=lambda item: item[0])
            },
            "verification_by_task_kind": {
                kind: dict(counter)
                for kind, counter in sorted(verification_by_kind.items(), key=lambda item: item[0])
            },
            "context_efficiency": {
                "sample_size": len(context_rows),
                "by_task_kind": context_by_kind,
            },
        }


operations_observability_service = OperationsObservabilityService()


def get_operations_observability_service() -> OperationsObservabilityService:
    return operations_observability_service
