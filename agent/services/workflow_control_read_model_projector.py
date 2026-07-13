"""Adapt legacy backend observations to canonical events and rebuild UI state."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol

from agent.services.workflow_runtime._serialization import redact_json, sha256_json
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.events import (
    CANONICAL_WORKFLOW_EVENT_SCHEMA,
    CanonicalWorkflowEvent,
    EventStore,
    InMemoryEventStore,
    LegacyWorkflowBackendEventAdapter,
)
from agent.services.workflow_runtime_operations_models import (
    WorkflowRuntimeOperationRecord,
)
from agent.services.workflow_runtime_read_model_service import (
    WorkflowRuntimeReadModelService,
)


class WorkflowControlProjectionBinding(Protocol):
    @property
    def tenant_id(self) -> str: ...

    @property
    def workflow_id(self) -> str: ...

    @property
    def run_id(self) -> str: ...

    @property
    def request(self) -> Any: ...


class WorkflowControlReadModelProjector:
    """Append observations first; project operations state only from EventStore."""

    def __init__(
        self,
        read_models: WorkflowRuntimeReadModelService,
        *,
        event_store: EventStore | None = None,
    ) -> None:
        self._read_models = read_models
        self._events = event_store or InMemoryEventStore()

    @property
    def event_store(self) -> EventStore:
        return self._events

    def project(
        self,
        *,
        binding: WorkflowControlProjectionBinding,
        status: Mapping[str, Any],
        runtime: str,
        mode: str,
        capabilities: Sequence[str] = (),
        events: Sequence[Mapping[str, Any]] = (),
    ) -> WorkflowRuntimeOperationRecord:
        status_events = tuple(
            item for item in status.get("events") or () if isinstance(item, Mapping)
        )
        observed = (*status_events, *events)
        correlation_id = str(
            getattr(binding.request, "correlation_id", "") or binding.run_id
        )
        for raw in observed:
            candidate = self._adapt_event(
                raw,
                binding=binding,
                correlation_id=correlation_id,
            )
            self._append(candidate)

        snapshot = self._snapshot_event(
            binding=binding,
            status=status,
            runtime=runtime,
            mode=mode,
            capabilities=capabilities,
            observed_events=observed,
            correlation_id=correlation_id,
        )
        self._append(snapshot)
        canonical = self._events.list_events(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
        )
        # No backend snapshot or mutable projection is consulted here. The SQL
        # read model is a disposable cache rebuilt from the append-only stream.
        return self._read_models.record_from_events(canonical, runtime_metadata={})

    def rebuild(
        self,
        *,
        tenant_id: str,
        run_id: str,
    ) -> WorkflowRuntimeOperationRecord:
        canonical = self._events.list_events(
            tenant_id=tenant_id,
            run_id=run_id,
        )
        return self._read_models.record_from_events(canonical, runtime_metadata={})

    def project_canonical(
        self,
        *,
        binding: WorkflowControlProjectionBinding,
        status: Mapping[str, Any],
        runtime: str,
        mode: str,
        capabilities: Sequence[str] = (),
    ) -> WorkflowRuntimeOperationRecord:
        """Project a runtime that already owns canonical Hub events.

        Native orchestration and its checkpoint state share the canonical
        sequence. Synthetic observation events must not interleave with that
        sequence, so this path only rebuilds the disposable read model.
        """

        canonical = self._events.list_events(
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
        )
        metadata = _runtime_observation(
            binding=binding,
            status=status,
            runtime=runtime,
            mode=mode,
            capabilities=capabilities,
        )
        return self._read_models.record_from_events(
            canonical,
            runtime_metadata=metadata,
        )

    def _adapt_event(
        self,
        raw: Mapping[str, Any],
        *,
        binding: WorkflowControlProjectionBinding,
        correlation_id: str,
    ) -> CanonicalWorkflowEvent:
        value = dict(raw)
        if value.get("schema") == CANONICAL_WORKFLOW_EVENT_SCHEMA:
            event = CanonicalWorkflowEvent.from_mapping(value)
            if (
                event.tenant_id != binding.tenant_id
                or event.workflow_id != binding.workflow_id
                or event.run_id != binding.run_id
            ):
                raise ValueError("workflow_control_event_binding_mismatch")
            return replace(event, sequence=0)
        value.setdefault("workflow_id", binding.workflow_id)
        return LegacyWorkflowBackendEventAdapter.adapt(
            value,
            tenant_id=binding.tenant_id,
            run_id=binding.run_id,
            correlation_id=correlation_id,
            causation_id=f"legacy-backend:{binding.run_id}",
        )

    def _snapshot_event(
        self,
        *,
        binding: WorkflowControlProjectionBinding,
        status: Mapping[str, Any],
        runtime: str,
        mode: str,
        capabilities: Sequence[str],
        observed_events: Sequence[Mapping[str, Any]],
        correlation_id: str,
    ) -> CanonicalWorkflowEvent:
        safe_status = {
            str(key): value
            for key, value in status.items()
            if key != "events"
        }
        observation = _runtime_observation(
            binding=binding,
            status=safe_status,
            runtime=runtime,
            mode=mode,
            capabilities=capabilities,
        )
        identity = sha256_json(
            {
                "binding": {
                    "tenant_id": binding.tenant_id,
                    "workflow_id": binding.workflow_id,
                    "run_id": binding.run_id,
                },
                "observation": observation,
                "source_event_ids": [
                    str(item.get("event_id") or sha256_json(redact_json(dict(item))))
                    for item in observed_events
                ],
            }
        )
        observed_status = str(status.get("status") or "unknown").strip().lower()
        event_type = {
            "running": "workflow.run.started",
            "completed": "workflow.run.completed",
            "succeeded": "workflow.run.completed",
            "success": "workflow.run.completed",
            "failed": "workflow.run.failed",
            "cancelled": "workflow.run.cancelled",
            "canceled": "workflow.run.cancelled",
        }.get(observed_status, "workflow.run.status.changed")
        return CanonicalWorkflowEvent.build(
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            event_type=event_type,
            correlation_id=correlation_id,
            causation_id=f"legacy-snapshot:{identity}",
            dedupe_key=f"legacy-snapshot:{identity}",
            actor="hub-legacy-adapter",
            payload={
                "status": observed_status,
                "source": "legacy_workflow_backend_snapshot",
                "runtime_observation": observation,
            },
            occurred_at=_observation_timestamp(status, observed_events, identity),
            event_id=f"wfe-legacy-snapshot-{identity}",
        )

    def _append(self, candidate: CanonicalWorkflowEvent) -> CanonicalWorkflowEvent:
        for _ in range(4):
            current = self._events.list_events(
                tenant_id=candidate.tenant_id,
                run_id=candidate.run_id,
            )
            duplicate = next(
                (
                    event
                    for event in current
                    if event.dedupe_key == candidate.dedupe_key
                ),
                None,
            )
            if duplicate is not None:
                if duplicate.content_hash != candidate.content_hash:
                    raise OptimisticConcurrencyError("dedupe_key_payload_conflict")
                return duplicate
            try:
                return self._events.append(
                    candidate,
                    expected_sequence=len(current),
                )
            except OptimisticConcurrencyError as exc:
                if "event_sequence_conflict" not in str(exc):
                    raise
        raise OptimisticConcurrencyError("workflow_control_projection_cas_exhausted")


def _runtime_observation(
    *,
    binding: WorkflowControlProjectionBinding,
    status: Mapping[str, Any],
    runtime: str,
    mode: str,
    capabilities: Sequence[str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": str(
            status.get("hub_task_id")
            or status.get("task_id")
            or binding.run_id
        ),
        "runtime": str(runtime),
        "mode": str(mode),
        "stale_after_seconds": 300.0,
        "degraded": bool(status.get("degraded", False)),
    }
    if capabilities:
        payload["capabilities"] = list(capabilities)
    for key, value in (
        ("fallbacks", _items(status.get("fallbacks"))),
        ("evidence", _items(status.get("evidence"))),
        ("parity_gaps", _items(status.get("parity_gaps"))),
        ("semantic_deviations", _items(status.get("semantic_deviations"))),
        ("gates", _gates(status)),
    ):
        if value:
            payload[key] = value
    for key in ("cost_micros", "latency_ms"):
        if status.get(key) is not None:
            payload[key] = status.get(key)
    if isinstance(status.get("recovery"), Mapping):
        payload["recovery"] = dict(status["recovery"])
    return dict(redact_json(payload))


def _gates(status: Mapping[str, Any]) -> list[dict[str, Any]]:
    explicit = _items(status.get("gates"))
    if explicit:
        return explicit
    open_gates = status.get("open_gates")
    if isinstance(open_gates, (list, tuple, set, frozenset)):
        return [
            {"gate_id": str(gate_id), "label": str(gate_id), "status": "open"}
            for gate_id in open_gates
            if str(gate_id).strip()
        ]
    steps = status.get("steps")
    if not isinstance(steps, (list, tuple)):
        return []
    return [
        {
            "gate_id": str(step.get("step_id") or step.get("id")),
            "label": str(
                step.get("label") or step.get("step_id") or step.get("id")
            ),
            "status": (
                "open"
                if str(step.get("status") or "") == "waiting_for_approval"
                else "closed"
            ),
        }
        for step in steps
        if isinstance(step, Mapping)
        and bool(step.get("gate"))
        and str(step.get("step_id") or step.get("id") or "").strip()
    ]


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, (list, tuple)):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _observation_timestamp(
    status: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    identity: str,
) -> float:
    candidates = [
        status.get("occurred_at"),
        status.get("updated_at"),
        status.get("timestamp"),
        *(item.get("occurred_at") or item.get("timestamp") for item in events),
    ]
    valid: list[float] = []
    for value in candidates:
        try:
            timestamp = float(value or 0)
        except (TypeError, ValueError):
            continue
        if timestamp > 0:
            valid.append(timestamp)
    if valid:
        return max(valid)
    return float(1_700_000_000 + (int(identity[:12], 16) % 31_536_000))


__all__ = ["WorkflowControlReadModelProjector"]
