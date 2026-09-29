"""Rebuildable workflow run projection and the legacy backend event adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.services.workflow_runtime._serialization import redact_json, sha256_json
from agent.services.workflow_runtime.errors import ContractValidationError, OptimisticConcurrencyError
from agent.services.workflow_runtime.event_canonical import CanonicalWorkflowEvent
from agent.services.workflow_runtime.event_transition_payloads import _stable_legacy_timestamp


@dataclass
class WorkflowRunProjection:
    """Rebuildable operational read model derived only from canonical events."""

    tenant_id: str
    run_id: str
    workflow_id: str = ""
    status: str = "pending"
    sequence: int = 0
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    approvals: dict[str, dict[str, Any]] = field(default_factory=dict)
    budgets: dict[str, Any] = field(default_factory=dict)
    operations: dict[str, dict[str, Any]] = field(default_factory=dict)
    _seen_dedupe_keys: set[str] = field(default_factory=set, repr=False)

    def apply(self, event: CanonicalWorkflowEvent) -> bool:
        event.assert_valid()
        if event.tenant_id != self.tenant_id or event.run_id != self.run_id:
            raise ContractValidationError("projection_binding_mismatch")
        if event.dedupe_key in self._seen_dedupe_keys:
            return False
        if event.sequence != self.sequence + 1:
            raise OptimisticConcurrencyError(
                f"projection_sequence_gap:expected={self.sequence + 1}:actual={event.sequence}"
            )
        self.workflow_id = event.workflow_id
        event_type = event.event_type
        if event_type == "workflow.run.started":
            self.status = "running"
        elif event_type in {"workflow.run.completed", "workflow.run.failed", "workflow.run.cancelled"}:
            self.status = event_type.rsplit(".", 1)[-1]
        elif event_type == "workflow.run.status.changed":
            observed_status = str(event.payload.get("status") or "").strip().lower()
            if observed_status:
                self.status = observed_status
        elif event_type.startswith("workflow.step.") and event.step_id:
            step = dict(self.steps.get(event.step_id) or {})
            step.update(
                {
                    "status": event_type.rsplit(".", 1)[-1],
                    "attempt": event.attempt,
                    "sequence": event.sequence,
                    "payload": dict(event.payload),
                }
            )
            self.steps[event.step_id] = step
        elif event_type.startswith("workflow.approval."):
            gate_id = str(event.payload.get("gate_id") or event.step_id)
            if gate_id:
                self.approvals[gate_id] = {
                    "status": event_type.rsplit(".", 1)[-1],
                    "sequence": event.sequence,
                    "actor": event.actor,
                }
        elif event_type == "workflow.budget.updated":
            self.budgets.update(dict(event.payload))
        elif event_type.startswith("workflow.side_effect."):
            operation_id = str(event.payload.get("operation_id") or "")
            if operation_id:
                self.operations[operation_id] = {
                    "status": event_type.rsplit(".", 1)[-1],
                    "sequence": event.sequence,
                }
        self._seen_dedupe_keys.add(event.dedupe_key)
        self.sequence = event.sequence
        return True

    @classmethod
    def rebuild(
        cls,
        *,
        tenant_id: str,
        run_id: str,
        events: tuple[CanonicalWorkflowEvent, ...] | list[CanonicalWorkflowEvent],
    ) -> "WorkflowRunProjection":
        projection = cls(tenant_id=tenant_id, run_id=run_id)
        for event in sorted(events, key=lambda item: item.sequence):
            projection.apply(event)
        return projection

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ananta.workflow_run_projection.v1",
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "sequence": self.sequence,
            "steps": dict(self.steps),
            "approvals": dict(self.approvals),
            "budgets": dict(self.budgets),
            "operations": dict(self.operations),
        }


class LegacyWorkflowBackendEventAdapter:
    """Maps existing ``ananta.workflow_backend_event.v1`` dictionaries."""

    @staticmethod
    def adapt(
        raw: dict[str, Any],
        *,
        tenant_id: str,
        run_id: str,
        correlation_id: str,
        causation_id: str,
    ) -> CanonicalWorkflowEvent:
        details = dict(raw.get("details") or {})
        digest = sha256_json(redact_json(raw))
        legacy_type = str(raw.get("event_type") or "unknown").strip().replace("_", ".")
        event_type = legacy_type if legacy_type.startswith("workflow.") else f"workflow.legacy.{legacy_type}"
        return CanonicalWorkflowEvent.build(
            tenant_id=tenant_id,
            workflow_id=raw.get("workflow_id"),
            run_id=run_id,
            event_type=event_type,
            correlation_id=correlation_id,
            causation_id=causation_id,
            dedupe_key=str(raw.get("event_id") or digest),
            step_id=str(details.get("step_id") or ""),
            attempt=int(details.get("attempt") or 0),
            actor=str(raw.get("actor") or "system"),
            payload={"legacy_status": raw.get("status"), **details},
            occurred_at=float(raw.get("occurred_at") or raw.get("timestamp") or _stable_legacy_timestamp(digest)),
            event_id=str(raw.get("event_id") or f"wfe-legacy-{digest}"),
        )
