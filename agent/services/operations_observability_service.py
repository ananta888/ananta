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

    def _usage_context(self, event_details: dict[str, Any], config: dict[str, Any] | None) -> str:
        explicit = str(event_details.get("usage_context") or "").strip().lower()
        if explicit in {"demo", "trial", "production"}:
            return explicit

        runtime_profile = str(event_details.get("runtime_profile") or (config or {}).get("runtime_profile") or "").strip().lower()
        source = str(event_details.get("source") or "").strip().lower()
        mode = str(event_details.get("mode") or "").strip().lower()
        if source == "demo" or mode in {"guided-first-run", "demo"} or runtime_profile == "demo":
            return "demo"
        if runtime_profile in {"team-controlled", "secure-enterprise", "distributed-strict"} or source == "api":
            return "production"
        return "trial"

    def _product_event_summary(self, *, max_records: int, config: dict[str, Any] | None) -> dict[str, Any]:
        repos = get_repository_registry()
        logs = repos.audit_repo.get_all(limit=max_records)
        events: list[dict[str, Any]] = []
        for log in logs:
            if not str(log.action or "").startswith("product_"):
                continue
            details = dict(log.details or {})
            product_event = details.get("product_event")
            if isinstance(product_event, dict):
                events.append(product_event)

        counts = Counter(str(event.get("event_type") or "unknown") for event in events)
        total = max(1, len(events))
        blocked = counts.get("goal_blocked", 0)
        review = counts.get("review_required", 0)
        failed = counts.get("goal_planning_failed", 0)
        succeeded = counts.get("goal_planning_succeeded", 0)

        reasons = Counter()
        channel_counts = Counter()
        context_counts = Counter()
        friction_by_channel: dict[str, Counter] = defaultdict(Counter)
        friction_by_context: dict[str, Counter] = defaultdict(Counter)

        for event in events:
            event_type = str(event.get("event_type") or "unknown")
            details = dict(event.get("details") or {})
            source = str(details.get("source") or "unknown").strip().lower() or "unknown"
            usage_context = self._usage_context(details, config)
            channel_counts[source] += 1
            context_counts[usage_context] += 1
            friction_by_channel[source][event_type] += 1
            friction_by_context[usage_context][event_type] += 1
            reason = str(details.get("reason") or "").strip()
            if reason and event_type in {"goal_blocked", "goal_planning_failed", "review_required"}:
                reasons[reason] += 1

        def _rates(counter: Counter) -> dict[str, Any]:
            subtotal = max(1, sum(counter.values()))
            return {
                "total": sum(counter.values()),
                "blocked": counter.get("goal_blocked", 0),
                "review_required": counter.get("review_required", 0),
                "failed": counter.get("goal_planning_failed", 0),
                "succeeded": counter.get("goal_planning_succeeded", 0),
                "blocked_rate": round(counter.get("goal_blocked", 0) / subtotal, 4),
                "review_rate": round(counter.get("review_required", 0) / subtotal, 4),
                "failure_rate": round(counter.get("goal_planning_failed", 0) / subtotal, 4),
            }

        return {
            "sample_size": len(events),
            "counts_by_event": dict(counts),
            "friction": {
                "blocked": blocked,
                "review_required": review,
                "failed": failed,
                "succeeded": succeeded,
                "blocked_rate": round(blocked / total, 4),
                "review_rate": round(review / total, 4),
                "failure_rate": round(failed / total, 4),
                "top_reasons": _top(reasons),
            },
            "channels": {
                "counts": dict(channel_counts),
                "friction_by_source": {source: _rates(counter) for source, counter in sorted(friction_by_channel.items())},
            },
            "usage_contexts": {
                "counts": dict(context_counts),
                "friction_by_context": {context: _rates(counter) for context, counter in sorted(friction_by_context.items())},
            },
        }

    def build_dashboard_summary(self, *, tasks: list[dict], max_records: int = 300, config: dict[str, Any] | None = None) -> dict[str, Any]:
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
            "product_events": self._product_event_summary(max_records=max_records, config=config),
        }


operations_observability_service = OperationsObservabilityService()


def get_operations_observability_service() -> OperationsObservabilityService:
    return operations_observability_service
