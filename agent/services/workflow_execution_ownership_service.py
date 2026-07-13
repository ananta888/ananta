"""Observable Hub service around the execution-ownership state machine."""

from __future__ import annotations

from typing import Any

from agent.services.workflow_runtime import (
    EventStore,
    ExecutionOwnership,
    ExecutionOwnershipStore,
    OwnershipClaim,
    RetryBudgetSnapshot,
    ownership_event,
)


class WorkflowExecutionOwnershipService:
    """Add canonical events and an explicit manual-recovery command to a store."""

    def __init__(self, store: ExecutionOwnershipStore, events: EventStore) -> None:
        self._store = store
        self._events = events

    def claim(
        self,
        *,
        correlation_id: str = "",
        causation_id: str = "ownership-claim",
        **values: Any,
    ) -> OwnershipClaim:
        result = self._store.claim(**values)
        if result.acquired:
            self._record(
                result.ownership,
                correlation_id=correlation_id or result.ownership.run_id,
                causation_id=causation_id,
            )
        return result

    def heartbeat(
        self,
        *,
        correlation_id: str = "",
        causation_id: str = "ownership-heartbeat",
        **values: Any,
    ) -> ExecutionOwnership:
        result = self._store.heartbeat(**values)
        self._record(
            result,
            correlation_id=correlation_id or result.run_id,
            causation_id=causation_id,
        )
        return result

    def acknowledge_result(
        self,
        *,
        correlation_id: str = "",
        causation_id: str = "result-acknowledgement",
        **values: Any,
    ) -> ExecutionOwnership:
        result = self._store.acknowledge_result(**values)
        self._record(
            result,
            correlation_id=correlation_id or result.run_id,
            causation_id=causation_id,
        )
        return result

    def fail_attempt(
        self,
        *,
        correlation_id: str = "",
        causation_id: str = "attempt-failure",
        **values: Any,
    ) -> ExecutionOwnership:
        result = self._store.fail_attempt(**values)
        self._record(
            result,
            correlation_id=correlation_id or result.run_id,
            causation_id=causation_id,
        )
        return result

    def reconcile_orphan(
        self,
        *,
        correlation_id: str = "",
        causation_id: str = "orphan-reconciler",
        **values: Any,
    ) -> ExecutionOwnership | None:
        result = self._store.reconcile_orphan(**values)
        if result is not None:
            self._record(
                result,
                correlation_id=correlation_id or result.run_id,
                causation_id=causation_id,
            )
        return result

    def manual_resume(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        owner_id: str,
        lease_seconds: float,
        maximum_retries: int,
        command_id: str,
        correlation_id: str,
        now: float | None = None,
    ) -> OwnershipClaim:
        current = self._store.get(
            tenant_id=tenant_id,
            run_id=run_id,
            step_id=step_id,
        )
        if current is None or current.status not in {"failed", "orphaned", "dead_letter"}:
            raise ValueError("manual_resume_recovery_state_required")
        result = self._store.claim(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            owner_id=owner_id,
            lease_seconds=lease_seconds,
            maximum_retries=maximum_retries,
            now=now,
        )
        if not result.acquired or result.ownership.attempt_id == current.attempt_id:
            raise ValueError("manual_resume_claim_denied")
        self._record(
            result.ownership,
            correlation_id=correlation_id,
            causation_id=command_id,
        )
        return result

    def consume_retry(self, **values: Any) -> RetryBudgetSnapshot:
        return self._store.consume_retry(**values)

    def get_retry_budget(self, **values: Any) -> RetryBudgetSnapshot:
        return self._store.get_retry_budget(**values)

    def get(self, **values: Any) -> ExecutionOwnership | None:
        return self._store.get(**values)

    def _record(
        self,
        ownership: ExecutionOwnership,
        *,
        correlation_id: str,
        causation_id: str,
    ) -> None:
        event = ownership_event(
            ownership,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        current = self._events.list_events(
            tenant_id=ownership.tenant_id,
            run_id=ownership.run_id,
        )
        if any(item.dedupe_key == event.dedupe_key for item in current):
            return
        self._events.append(event, expected_sequence=len(current))


__all__ = ["WorkflowExecutionOwnershipService"]
