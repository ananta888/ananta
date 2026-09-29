"""Execution ownership record, claim, retry-budget snapshot, lease helpers, and the ownership store ports."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from agent.services.workflow_runtime.errors import FencingTokenError, OptimisticConcurrencyError
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent
from agent.services.workflow_runtime.ownership_transition_errors import WorkflowTransitionOwnershipReservationConflict
from agent.services.workflow_runtime.ownership_values import (
    EXECUTION_OWNERSHIP_SCHEMA,
    OWNERSHIP_STATUSES,
    RETRY_BUDGET_SCHEMA,
    _ownership_exact_schema,
    _ownership_finite_timestamp,
    _ownership_legacy_text,
    _ownership_positive_legacy_counter,
    _ownership_status,
)


def ownership_event(
    ownership: "ExecutionOwnership",
    *,
    correlation_id: str,
    causation_id: str,
    actor: str = "hub",
) -> CanonicalWorkflowEvent:
    """Map an atomically committed lease revision to a canonical event."""

    event_suffix = {
        "active": "ownership_claimed",
        "completed": "result_acknowledged",
        "failed": "attempt_failed",
        "orphaned": "orphaned",
        "dead_letter": "dead_lettered",
    }[ownership.status]
    return CanonicalWorkflowEvent.build(
        tenant_id=ownership.tenant_id,
        workflow_id=ownership.workflow_id,
        run_id=ownership.run_id,
        step_id=ownership.step_id,
        attempt=ownership.fencing_token,
        event_type=f"workflow.step.{event_suffix}",
        correlation_id=correlation_id,
        causation_id=causation_id,
        dedupe_key=(f"ownership:{ownership.run_id}:{ownership.step_id}:{ownership.attempt_id}:{ownership.revision}"),
        actor=actor,
        payload={
            "attempt_id": ownership.attempt_id,
            "owner_id": ownership.owner_id,
            "fencing_token": ownership.fencing_token,
            "lease_expires_at": ownership.lease_expires_at,
            "result_ack_key": ownership.result_ack_key,
            "failure_code": ownership.failure_code,
        },
        occurred_at=ownership.last_heartbeat_at,
        event_id=(f"wfe-ownership-{ownership.run_id}-{ownership.step_id}-{ownership.attempt_id}-{ownership.revision}"),
    )


@dataclass(frozen=True)
class ExecutionOwnership:
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    attempt_id: str
    owner_id: str
    fencing_token: int
    revision: int
    status: str
    lease_expires_at: float
    last_heartbeat_at: float
    result_ack_key: str = ""
    failure_code: str = ""
    schema: str = EXECUTION_OWNERSHIP_SCHEMA

    def assert_valid(self) -> None:
        required = (
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.step_id,
            self.attempt_id,
            self.owner_id,
        )
        if any(not value for value in required):
            raise ValueError("execution_ownership_binding_required")
        if self.status not in OWNERSHIP_STATUSES:
            raise ValueError("execution_ownership_status_invalid")
        if self.fencing_token < 1 or self.revision < 1:
            raise ValueError("execution_ownership_fencing_invalid")
        if self.schema != EXECUTION_OWNERSHIP_SCHEMA:
            raise ValueError("execution_ownership_schema_unsupported")

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "ExecutionOwnership":
        value = cls(
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            step_id=str(raw.get("step_id") or ""),
            attempt_id=str(raw.get("attempt_id") or ""),
            owner_id=str(raw.get("owner_id") or ""),
            fencing_token=int(raw.get("fencing_token") or 0),
            revision=int(raw.get("revision") or 0),
            status=str(raw.get("status") or ""),
            lease_expires_at=float(raw.get("lease_expires_at") or 0),
            last_heartbeat_at=float(raw.get("last_heartbeat_at") or 0),
            result_ack_key=str(raw.get("result_ack_key") or ""),
            failure_code=str(raw.get("failure_code") or ""),
            schema=str(raw.get("schema") or EXECUTION_OWNERSHIP_SCHEMA),
        )
        value.assert_valid()
        return value

    @classmethod
    def from_exact_mapping(cls, raw: Mapping[str, Any]) -> "ExecutionOwnership":
        """Hydrate transition authority without defaults, coercion, or aliases."""

        fields = {
            "schema",
            "tenant_id",
            "workflow_id",
            "run_id",
            "step_id",
            "attempt_id",
            "owner_id",
            "fencing_token",
            "revision",
            "status",
            "lease_expires_at",
            "last_heartbeat_at",
            "result_ack_key",
            "failure_code",
        }
        if not isinstance(raw, Mapping) or set(raw) != fields:
            raise ValueError("execution_ownership_exact_mapping_invalid")
        value = cls(
            tenant_id=_ownership_legacy_text(raw["tenant_id"], "tenant_id", empty=False),
            workflow_id=_ownership_legacy_text(raw["workflow_id"], "workflow_id", empty=False),
            run_id=_ownership_legacy_text(raw["run_id"], "run_id", empty=False),
            step_id=_ownership_legacy_text(raw["step_id"], "step_id", empty=False),
            attempt_id=_ownership_legacy_text(raw["attempt_id"], "attempt_id", empty=False),
            owner_id=_ownership_legacy_text(raw["owner_id"], "owner_id", empty=False),
            fencing_token=_ownership_positive_legacy_counter(raw["fencing_token"], "fencing_token"),
            revision=_ownership_positive_legacy_counter(raw["revision"], "revision"),
            status=_ownership_status(raw["status"]),
            lease_expires_at=_ownership_finite_timestamp(raw["lease_expires_at"], "lease_expires_at"),
            last_heartbeat_at=_ownership_finite_timestamp(raw["last_heartbeat_at"], "last_heartbeat_at"),
            result_ack_key=_ownership_legacy_text(raw["result_ack_key"], "result_ack_key", empty=True),
            failure_code=_ownership_legacy_text(raw["failure_code"], "failure_code", empty=True),
            schema=_ownership_exact_schema(raw["schema"], EXECUTION_OWNERSHIP_SCHEMA),
        )
        if value.status == "active" and (value.result_ack_key or value.failure_code):
            raise ValueError("execution_ownership_active_terminal_fields_invalid")
        if value.status == "completed" and (not value.result_ack_key or value.failure_code):
            raise ValueError("execution_ownership_completion_fields_invalid")
        if value.status in {"failed", "orphaned", "dead_letter"} and (not value.failure_code or value.result_ack_key):
            raise ValueError("execution_ownership_failure_fields_invalid")
        return value

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "attempt_id": self.attempt_id,
            "owner_id": self.owner_id,
            "fencing_token": self.fencing_token,
            "revision": self.revision,
            "status": self.status,
            "lease_expires_at": self.lease_expires_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "result_ack_key": self.result_ack_key,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True)
class OwnershipClaim:
    ownership: ExecutionOwnership
    acquired: bool
    reason: str


@dataclass(frozen=True)
class RetryBudgetSnapshot:
    tenant_id: str
    run_id: str
    used: int
    maximum: int
    schema: str = RETRY_BUDGET_SCHEMA

    @property
    def remaining(self) -> int:
        return max(0, self.maximum - self.used)


class RetryBudgetOwner(Protocol):
    def consume_retry(
        self,
        *,
        tenant_id: str,
        run_id: str,
        retry_id: str,
        category: str,
        maximum: int,
    ) -> RetryBudgetSnapshot: ...

    def get_retry_budget(self, *, tenant_id: str, run_id: str, maximum: int) -> RetryBudgetSnapshot: ...


class ExecutionOwnershipStore(RetryBudgetOwner, Protocol):
    def claim(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        owner_id: str,
        lease_seconds: float,
        maximum_retries: int,
        now: float | None = None,
    ) -> OwnershipClaim: ...

    def heartbeat(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        expected_revision: int,
        lease_seconds: float,
        now: float | None = None,
    ) -> ExecutionOwnership: ...

    def acknowledge_result(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        expected_revision: int,
        result_ack_key: str,
        now: float | None = None,
    ) -> ExecutionOwnership: ...

    def fail_attempt(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        expected_revision: int,
        failure_code: str,
        dead_letter: bool = False,
        now: float | None = None,
    ) -> ExecutionOwnership: ...

    def reconcile_orphan(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        now: float | None = None,
    ) -> ExecutionOwnership | None: ...

    def get(self, *, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None: ...


def _exact_optional_ownership(value: object, reason: str) -> ExecutionOwnership | None:
    if value is None:
        return None
    if not isinstance(value, ExecutionOwnership):
        raise WorkflowTransitionOwnershipReservationConflict(f"workflow_transition_ownership_{reason}_invalid")
    return ExecutionOwnership.from_exact_mapping(value.to_dict())


def _validate_lease(lease_seconds: float, now: Any) -> float:
    if float(lease_seconds) <= 0:
        raise ValueError("lease_seconds_invalid")
    return float(now if now is not None else time.time())


def _timestamp(value: Any) -> float:
    return float(time.time() if value is None else value)


def _assert_owner(current: ExecutionOwnership, *, attempt_id: str, owner_id: str, fencing_token: int) -> None:
    if current.attempt_id != attempt_id or current.owner_id != owner_id or current.fencing_token != int(fencing_token):
        raise FencingTokenError("execution_owner_stale")


def _assert_owner_from_values(current: ExecutionOwnership, values: dict[str, Any]) -> None:
    _assert_owner(
        current,
        attempt_id=str(values["attempt_id"]),
        owner_id=str(values["owner_id"]),
        fencing_token=int(values["fencing_token"]),
    )


def _assert_expected_revision(current: ExecutionOwnership, expected_revision: int) -> None:
    if current.revision != int(expected_revision):
        raise OptimisticConcurrencyError(
            f"execution_ownership_revision_conflict:expected={expected_revision}:actual={current.revision}"
        )


def _heartbeat(current: ExecutionOwnership, *, values: dict[str, Any], timestamp: float) -> ExecutionOwnership:
    _assert_expected_revision(current, int(values["expected_revision"]))
    if current.status != "active":
        raise FencingTokenError("heartbeat_owner_no_longer_active")
    if current.lease_expires_at <= timestamp:
        raise FencingTokenError("ownership_lease_expired")
    return replace(
        current,
        revision=current.revision + 1,
        last_heartbeat_at=timestamp,
        lease_expires_at=timestamp + float(values["lease_seconds"]),
    )
