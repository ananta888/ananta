"""Hub-owned workflow-runtime evaluation and operations read model.

The service is the sole projection boundary consumed by HTTP/UI clients.  It
accepts canonical Hub observations, never reaches into workers or Temporal, and
keeps storage behind a small repository port so a durable projection can be
substituted without changing routes or Angular contracts.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent, WorkflowRunProjection
from agent.services.workflow_runtime_operations_models import WorkflowRuntimeOperationRecord

RUNTIME_OPERATIONS_LIST_SCHEMA = "ananta.workflow_runtime_operations_list.v1"


class WorkflowRuntimeReadModelRepository(Protocol):
    def upsert(self, record: WorkflowRuntimeOperationRecord) -> WorkflowRuntimeOperationRecord:
        ...

    def get(self, *, tenant_id: str, run_id: str) -> WorkflowRuntimeOperationRecord | None:
        ...

    def list_for_tenant(self, *, tenant_id: str) -> tuple[WorkflowRuntimeOperationRecord, ...]:
        ...


class InMemoryWorkflowRuntimeReadModelRepository:
    """Thread-safe reference repository keyed by tenant and run."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], WorkflowRuntimeOperationRecord] = {}
        self._lock = threading.RLock()

    def upsert(self, record: WorkflowRuntimeOperationRecord) -> WorkflowRuntimeOperationRecord:
        key = (record.tenant_id, record.run_id)
        with self._lock:
            current = self._records.get(key)
            if current is not None:
                if record.source_sequence < current.source_sequence:
                    raise ValueError("runtime_read_model_sequence_regression")
                if (
                    record.source_sequence == current.source_sequence
                    and record.updated_at < current.updated_at
                ):
                    return current
            self._records[key] = record
            return record

    def get(self, *, tenant_id: str, run_id: str) -> WorkflowRuntimeOperationRecord | None:
        with self._lock:
            return self._records.get((str(tenant_id), str(run_id)))

    def list_for_tenant(self, *, tenant_id: str) -> tuple[WorkflowRuntimeOperationRecord, ...]:
        with self._lock:
            values = [record for (tenant, _), record in self._records.items() if tenant == str(tenant_id)]
        return tuple(sorted(values, key=lambda item: (-item.updated_at, item.run_id)))

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


@dataclass(frozen=True)
class RuntimeOperationsQuery:
    runtime: str = ""
    mode: str = ""
    status: str = ""
    health: str = ""
    search: str = ""
    limit: int = 100

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RuntimeOperationsQuery":
        health = str(value.get("health") or "").strip().lower()
        allowed_health = {"", "healthy", "degraded", "stale", "parity_gap", "unverified"}
        if health not in allowed_health:
            raise ValueError("runtime_operations_health_filter_invalid")
        try:
            limit = int(value.get("limit") or 100)
        except (TypeError, ValueError) as exc:
            raise ValueError("runtime_operations_limit_invalid") from exc
        if not 1 <= limit <= 500:
            raise ValueError("runtime_operations_limit_invalid")
        return cls(
            runtime=str(value.get("runtime") or "").strip().lower()[:64],
            mode=str(value.get("mode") or "").strip().lower()[:64],
            status=str(value.get("status") or "").strip().lower()[:64],
            health=health,
            search=str(value.get("q") or value.get("search") or "").strip().lower()[:160],
            limit=limit,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime or None,
            "mode": self.mode or None,
            "status": self.status or None,
            "health": self.health or None,
            "q": self.search or None,
            "limit": self.limit,
        }


class WorkflowRuntimeReadModelService:
    """Projects canonical observations into tenant-scoped UI read models."""

    def __init__(self, repository: WorkflowRuntimeReadModelRepository | None = None) -> None:
        self._repository = repository or InMemoryWorkflowRuntimeReadModelRepository()

    def record_snapshot(
        self,
        snapshot: WorkflowRuntimeOperationRecord | Mapping[str, Any],
    ) -> WorkflowRuntimeOperationRecord:
        record = (
            snapshot
            if isinstance(snapshot, WorkflowRuntimeOperationRecord)
            else WorkflowRuntimeOperationRecord.from_mapping(snapshot)
        )
        return self._repository.upsert(record)

    def record_from_events(
        self,
        events: Sequence[CanonicalWorkflowEvent],
        *,
        runtime_metadata: Mapping[str, Any],
    ) -> WorkflowRuntimeOperationRecord:
        """Rebuild a snapshot solely from canonical Hub events plus runtime facts.

        Runtime implementations publish facts to the Hub event stream; this
        projection method deliberately has no SDK- or worker-facing dependency.
        """

        ordered = tuple(sorted(events, key=lambda item: item.sequence))
        if not ordered:
            raise ValueError("runtime_events_required")
        first = ordered[0]
        if any(event.tenant_id != first.tenant_id or event.run_id != first.run_id for event in ordered):
            raise ValueError("runtime_event_binding_mismatch")
        projection = WorkflowRunProjection.rebuild(
            tenant_id=first.tenant_id,
            run_id=first.run_id,
            events=list(ordered),
        )

        projected_metadata = dict(runtime_metadata)
        evidence = list(projected_metadata.get("evidence") or [])
        fallbacks = list(projected_metadata.get("fallbacks") or [])
        parity_gaps = list(projected_metadata.get("parity_gaps") or [])
        semantic_deviations = list(
            projected_metadata.get("semantic_deviations") or []
        )
        recovery = dict(projected_metadata.get("recovery") or {})
        capabilities = projected_metadata.get("capabilities") or []
        gates = list(projected_metadata.get("gates") or [])
        cost_micros = int(projected_metadata.get("cost_micros") or 0)
        latency_ms = float(projected_metadata.get("latency_ms") or 0.0)

        for event in ordered:
            payload = dict(event.payload)
            observation = payload.get("runtime_observation")
            if isinstance(observation, Mapping):
                observed = dict(observation)
                for key in (
                    "task_id",
                    "runtime",
                    "mode",
                    "stale_after_seconds",
                    "degraded",
                ):
                    if key in observed:
                        projected_metadata[key] = observed[key]
                if observed.get("capabilities"):
                    capabilities = list(observed["capabilities"])
                if observed.get("gates"):
                    gates = list(observed["gates"])
                evidence.extend(list(observed.get("evidence") or []))
                fallbacks.extend(list(observed.get("fallbacks") or []))
                parity_gaps.extend(list(observed.get("parity_gaps") or []))
                semantic_deviations.extend(
                    list(observed.get("semantic_deviations") or [])
                )
                if isinstance(observed.get("recovery"), Mapping):
                    recovery.update(dict(observed["recovery"]))
                cost_micros = max(
                    cost_micros,
                    int(observed.get("cost_micros") or 0),
                )
                latency_ms = max(
                    latency_ms,
                    float(observed.get("latency_ms") or 0.0),
                )
            if event.event_type == "workflow.evidence.recorded":
                evidence.append(payload)
            elif "fallback" in event.event_type:
                fallbacks.append(payload)
            elif event.event_type == "workflow.parity.gap":
                parity_gaps.append(payload)
            elif event.event_type == "workflow.semantic.deviation":
                semantic_deviations.append(payload)
            elif event.event_type.startswith("workflow.recovery."):
                recovery.update(payload)
                recovery["status"] = event.event_type.rsplit(".", 1)[-1]
            elif event.event_type == "workflow.cost.updated":
                cost_micros = max(cost_micros, int(payload.get("cost_micros") or 0))
            elif event.event_type == "workflow.latency.updated":
                latency_ms = max(latency_ms, float(payload.get("latency_ms") or 0.0))

        known_gate_ids = {
            str(item.get("gate_id") or item.get("id") or "")
            for item in gates
            if isinstance(item, Mapping)
        }
        for gate_id, approval in projection.approvals.items():
            if gate_id not in known_gate_ids:
                gates.append({"gate_id": gate_id, "label": gate_id, **approval})

        payload = {
            **projected_metadata,
            "tenant_id": first.tenant_id,
            "run_id": first.run_id,
            "workflow_id": projection.workflow_id,
            "status": projection.status,
            "source_sequence": projection.sequence,
            "updated_at": max(event.occurred_at for event in ordered),
            "capabilities": capabilities,
            "fallbacks": _dedupe_projection_facts(fallbacks),
            "cost_micros": cost_micros,
            "latency_ms": latency_ms,
            "recovery": recovery,
            "gates": gates,
            "evidence": _dedupe_projection_facts(evidence),
            "parity_gaps": _dedupe_projection_facts(parity_gaps),
            "semantic_deviations": _dedupe_projection_facts(
                semantic_deviations
            ),
        }
        return self.record_snapshot(payload)

    def get_record(self, *, tenant_id: str, run_id: str) -> WorkflowRuntimeOperationRecord | None:
        return self._repository.get(tenant_id=tenant_id, run_id=run_id)

    def get_run(self, *, tenant_id: str, run_id: str, now: float | None = None) -> dict[str, Any] | None:
        record = self.get_record(tenant_id=tenant_id, run_id=run_id)
        return record.to_dict(now=now) if record is not None else None

    def list_runs(
        self,
        *,
        tenant_id: str,
        query: RuntimeOperationsQuery | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        evaluated_at = float(now if now is not None else time.time())
        active_query = query or RuntimeOperationsQuery()
        records = [
            record
            for record in self._repository.list_for_tenant(tenant_id=tenant_id)
            if self._matches(record, active_query, now=evaluated_at)
        ][: active_query.limit]
        runs = [record.to_dict(now=evaluated_at) for record in records]
        return {
            "schema": RUNTIME_OPERATIONS_LIST_SCHEMA,
            "generated_at": evaluated_at,
            "filters": active_query.to_dict(),
            "summary": self._summarize(runs),
            "runs": runs,
            "count": len(runs),
        }

    @staticmethod
    def _matches(
        record: WorkflowRuntimeOperationRecord,
        query: RuntimeOperationsQuery,
        *,
        now: float,
    ) -> bool:
        if query.runtime and record.runtime != query.runtime:
            return False
        if query.mode and record.mode != query.mode:
            return False
        if query.status and record.status != query.status:
            return False
        if query.search:
            haystack = " ".join(
                (record.run_id, record.task_id, record.workflow_id, record.runtime, record.mode)
            ).lower()
            if query.search not in haystack:
                return False
        if query.health == "healthy" and (record.degraded or record.is_stale(now=now)):
            return False
        if query.health == "degraded" and not record.degraded:
            return False
        if query.health == "stale" and not record.is_stale(now=now):
            return False
        if query.health == "parity_gap" and not (record.parity_gaps or record.semantic_deviations):
            return False
        if query.health == "unverified" and not record.completed_without_evidence:
            return False
        return True

    @staticmethod
    def _summarize(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        latencies = sorted(float(item.get("latency_ms") or 0.0) for item in runs)

        def percentile(values: Sequence[float], ratio: float) -> float:
            if not values:
                return 0.0
            index = max(0, min(len(values) - 1, math.ceil(len(values) * ratio) - 1))
            return round(values[index], 3)

        return {
            "total_runs": len(runs),
            "degraded_runs": sum(bool(item.get("degraded")) for item in runs),
            "stale_runs": sum(bool(item.get("stale")) for item in runs),
            "unverified_successes": sum(item.get("outcome_claim") == "unverified" for item in runs),
            "open_gates": sum(int(item.get("open_gate_count") or 0) for item in runs),
            "verified_evidence": sum(int(item.get("verified_evidence_count") or 0) for item in runs),
            "total_cost_micros": sum(int(item.get("cost_micros") or 0) for item in runs),
            "latency_p50_ms": percentile(latencies, 0.50),
            "latency_p95_ms": percentile(latencies, 0.95),
            "active_recoveries": sum(
                str((item.get("recovery") or {}).get("status") or "")
                in {"active", "running", "retrying", "recovering"}
                for item in runs
            ),
            "parity_gap_runs": sum(bool(item.get("parity_gaps") or item.get("semantic_deviations")) for item in runs),
        }


def _dedupe_projection_facts(values: Sequence[Any]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, Mapping):
            continue
        fact = dict(value)
        identity = str(
            fact.get("evidence_id")
            or fact.get("id")
            or fact.get("code")
            or fact.get("reason_code")
            or sha256_json(fact)
        )
        result[identity] = fact
    return list(result.values())


_service_lock = threading.RLock()
_service: WorkflowRuntimeReadModelService | None = None


def get_workflow_runtime_read_model_service() -> WorkflowRuntimeReadModelService:
    """Return the process facade backed by the Hub's durable SQL projection."""

    global _service
    if _service is not None:
        return _service
    with _service_lock:
        if _service is None:
            from agent.database import engine
            from agent.services.workflow_runtime_read_model_persistence import (
                SQLAlchemyWorkflowRuntimeReadModelRepository,
            )

            _service = WorkflowRuntimeReadModelService(
                SQLAlchemyWorkflowRuntimeReadModelRepository(engine)
            )
    return _service


def reset_workflow_runtime_read_model_service() -> None:
    """Test/support hook; production request handling never clears projections."""

    with _service_lock:
        service = _service
        if service is None:
            return
        clear = getattr(service._repository, "clear", None)
        if callable(clear):
            clear()


__all__ = [
    "InMemoryWorkflowRuntimeReadModelRepository",
    "RUNTIME_OPERATIONS_LIST_SCHEMA",
    "RuntimeOperationsQuery",
    "WorkflowRuntimeReadModelRepository",
    "WorkflowRuntimeReadModelService",
    "get_workflow_runtime_read_model_service",
    "reset_workflow_runtime_read_model_service",
]
