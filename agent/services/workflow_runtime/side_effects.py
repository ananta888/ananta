"""Hub-owned idempotency and side-effect ledger.

Tool, Native, LangGraph, and Temporal adapters use the same sequence:

1. derive ``operation_id_for(tenant, run, step, declared_operation)``;
2. ``plan`` and ``authorize`` it with the current step fencing token;
3. atomically ``claim`` before the external call;
4. ``complete``, ``fail``, or ``mark_uncertain`` using the same attempt/fence.

A crash after the external call is intentionally represented as ``uncertain`` and
never automatically re-executed. Exactly-once *decision* is provided at the hub;
the stable operation ID must also be used as downstream idempotency key whenever
the external system supports it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.errors import (
    FencingTokenError,
    InvalidTransitionError,
    OptimisticConcurrencyError,
)
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent
from ananta_contracts.workflow_operation import operation_id_for

SIDE_EFFECT_LEDGER_SCHEMA = "ananta.side_effect_ledger.v1"
SIDE_EFFECT_CLASSES = frozenset({"read", "idempotent_write", "non_idempotent_write"})
SIDE_EFFECT_STATUSES = frozenset(
    {"planned", "authorized", "started", "completed", "failed", "uncertain", "compensated"}
)
_TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"authorized", "failed"}),
    "authorized": frozenset({"started", "failed"}),
    "started": frozenset({"completed", "failed", "uncertain"}),
    "failed": frozenset({"authorized", "compensated"}),
    "uncertain": frozenset({"completed", "failed", "compensated"}),
    "completed": frozenset({"compensated"}),
    "compensated": frozenset(),
}


def side_effect_event(
    record: "SideEffectRecord",
    *,
    correlation_id: str,
    causation_id: str,
    actor: str = "hub",
) -> CanonicalWorkflowEvent:
    """Map a committed ledger revision to a deduplicable canonical event."""

    return CanonicalWorkflowEvent.build(
        tenant_id=record.tenant_id,
        workflow_id=record.workflow_id,
        run_id=record.run_id,
        step_id=record.step_id,
        event_type=f"workflow.side_effect.{record.status}",
        correlation_id=correlation_id,
        causation_id=causation_id,
        dedupe_key=f"side-effect:{record.operation_id}:{record.revision}",
        actor=actor,
        payload={
            "operation_id": record.operation_id,
            "declared_operation": record.declared_operation,
            "side_effect_class": record.side_effect_class,
            "fencing_token": record.fencing_token,
            "attempt_id": record.attempt_id,
            "result_ref": record.result_ref,
            "failure_code": record.failure_code,
        },
        occurred_at=record.updated_at,
        event_id=f"wfe-side-effect-{record.operation_id}-{record.revision}",
    )


@dataclass(frozen=True)
class SideEffectRecord:
    operation_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    declared_operation: str
    side_effect_class: str
    status: str = "planned"
    revision: int = 1
    fencing_token: int = 0
    attempt_id: str = ""
    authorization_envelope_id: str = ""
    result_ref: str = ""
    failure_code: str = ""
    updated_at: float = 0.0
    schema: str = SIDE_EFFECT_LEDGER_SCHEMA

    def assert_valid(self) -> None:
        required = (
            self.operation_id,
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.step_id,
            self.declared_operation,
            self.side_effect_class,
        )
        if any(not value for value in required):
            raise ValueError("side_effect_binding_required")
        expected_id = operation_id_for(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            step_id=self.step_id,
            declared_operation=self.declared_operation,
        )
        if self.operation_id != expected_id:
            raise ValueError("side_effect_operation_id_invalid")
        if self.status not in SIDE_EFFECT_STATUSES or self.revision < 1 or self.fencing_token < 0:
            raise ValueError("side_effect_state_invalid")
        if self.side_effect_class not in SIDE_EFFECT_CLASSES:
            raise ValueError("side_effect_class_invalid")
        if self.schema != SIDE_EFFECT_LEDGER_SCHEMA:
            raise ValueError("side_effect_schema_unsupported")

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "SideEffectRecord":
        record = cls(
            operation_id=str(raw.get("operation_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            step_id=str(raw.get("step_id") or ""),
            declared_operation=str(raw.get("declared_operation") or ""),
            side_effect_class=str(raw.get("side_effect_class") or ""),
            status=str(raw.get("status") or "planned"),
            revision=int(raw.get("revision") or 0),
            fencing_token=int(raw.get("fencing_token") or 0),
            attempt_id=str(raw.get("attempt_id") or ""),
            authorization_envelope_id=str(raw.get("authorization_envelope_id") or ""),
            result_ref=str(raw.get("result_ref") or ""),
            failure_code=str(raw.get("failure_code") or ""),
            updated_at=float(raw.get("updated_at") or 0),
            schema=str(raw.get("schema") or SIDE_EFFECT_LEDGER_SCHEMA),
        )
        record.assert_valid()
        return record

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "operation_id": self.operation_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "declared_operation": self.declared_operation,
            "side_effect_class": self.side_effect_class,
            "status": self.status,
            "revision": self.revision,
            "fencing_token": self.fencing_token,
            "attempt_id": self.attempt_id,
            "authorization_envelope_id": self.authorization_envelope_id,
            "result_ref": self.result_ref,
            "failure_code": self.failure_code,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SideEffectClaim:
    record: SideEffectRecord
    acquired: bool
    reason: str


class SideEffectLedger(Protocol):
    def plan(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        declared_operation: str,
        side_effect_class: str,
    ) -> SideEffectRecord: ...

    def authorize(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        authorization_envelope_id: str,
    ) -> SideEffectRecord: ...

    def claim(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
    ) -> SideEffectClaim: ...

    def complete(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        result_ref: str,
    ) -> SideEffectRecord: ...

    def fail(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str,
    ) -> SideEffectRecord: ...

    def mark_uncertain(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str = "outcome_unknown",
    ) -> SideEffectRecord: ...

    def reconcile_uncertain(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        failure_code: str = "owner_lost",
    ) -> SideEffectRecord: ...

    def compensate(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        result_ref: str,
    ) -> SideEffectRecord: ...

    def get(self, *, tenant_id: str, operation_id: str) -> SideEffectRecord | None: ...


class InMemorySideEffectLedger:
    def __init__(self) -> None:
        self._records: dict[str, SideEffectRecord] = {}
        self._lock = threading.RLock()

    def plan(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        declared_operation: str,
        side_effect_class: str,
    ) -> SideEffectRecord:
        record = _new_record(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            declared_operation=declared_operation,
            side_effect_class=side_effect_class,
        )
        with self._lock:
            existing = self._records.get(record.operation_id)
            if existing is not None:
                if _binding(existing) != _binding(record):
                    raise OptimisticConcurrencyError("operation_id_binding_conflict")
                return existing
            self._records[record.operation_id] = record
            return record

    def authorize(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        authorization_envelope_id: str,
    ) -> SideEffectRecord:
        if not authorization_envelope_id:
            raise ValueError("authorization_envelope_id_required")
        return self._mutate(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            to_status="authorized",
            authorization_envelope_id=str(authorization_envelope_id),
        )

    def claim(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
    ) -> SideEffectClaim:
        if not attempt_id:
            raise ValueError("attempt_id_required")
        with self._lock:
            current = self._required(operation_id)
            if current.status == "completed":
                return SideEffectClaim(current, False, "already_completed")
            if (
                current.status == "started"
                and current.fencing_token == fencing_token
                and current.attempt_id == attempt_id
            ):
                return SideEffectClaim(current, False, "already_claimed")
            updated = _transition(
                current,
                expected_revision=expected_revision,
                fencing_token=fencing_token,
                to_status="started",
                attempt_id=attempt_id,
                require_exact_fence=True,
            )
            self._records[operation_id] = updated
            return SideEffectClaim(updated, True, "acquired")

    def complete(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        result_ref: str,
    ) -> SideEffectRecord:
        return self._finish(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            to_status="completed",
            result_ref=str(result_ref),
        )

    def fail(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str,
    ) -> SideEffectRecord:
        return self._finish(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            to_status="failed",
            failure_code=str(failure_code or "operation_failed"),
        )

    def mark_uncertain(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str = "outcome_unknown",
    ) -> SideEffectRecord:
        return self._finish(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            to_status="uncertain",
            failure_code=failure_code,
        )

    def reconcile_uncertain(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        failure_code: str = "owner_lost",
    ) -> SideEffectRecord:
        return self._mutate(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            to_status="uncertain",
            failure_code=failure_code,
        )

    def compensate(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        result_ref: str,
    ) -> SideEffectRecord:
        return self._mutate(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            to_status="compensated",
            result_ref=result_ref,
        )

    def get(self, *, tenant_id: str, operation_id: str) -> SideEffectRecord | None:
        with self._lock:
            record = self._records.get(str(operation_id))
            return record if record and record.tenant_id == str(tenant_id) else None

    def _required(self, operation_id: str) -> SideEffectRecord:
        record = self._records.get(str(operation_id))
        if record is None:
            raise KeyError("side_effect_operation_not_found")
        return record

    def _mutate(self, operation_id: str, **changes: Any) -> SideEffectRecord:
        with self._lock:
            current = self._required(operation_id)
            updated = _transition(current, **changes)
            self._records[operation_id] = updated
            return updated

    def _finish(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        to_status: str,
        result_ref: str = "",
        failure_code: str = "",
    ) -> SideEffectRecord:
        with self._lock:
            current = self._required(operation_id)
            if current.attempt_id != str(attempt_id):
                raise FencingTokenError("side_effect_attempt_mismatch")
            updated = _transition(
                current,
                expected_revision=expected_revision,
                fencing_token=fencing_token,
                to_status=to_status,
                attempt_id=attempt_id,
                result_ref=result_ref,
                failure_code=failure_code,
                require_exact_fence=True,
            )
            self._records[operation_id] = updated
            return updated


class SQLiteSideEffectLedger:
    """SQLite reference ledger; every claim/finish is a ``BEGIN IMMEDIATE`` CAS."""

    def __init__(self, database: str | Path):
        self._connection = sqlite3.connect(str(database), timeout=30, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._lock = threading.RLock()
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_side_effect_ledger (
                operation_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL,
                fencing_token INTEGER NOT NULL,
                record_json TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_side_effect_tenant_run ON workflow_side_effect_ledger (tenant_id, run_id)"
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def plan(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        declared_operation: str,
        side_effect_class: str,
    ) -> SideEffectRecord:
        record = _new_record(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            declared_operation=declared_operation,
            side_effect_class=side_effect_class,
        )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._read(record.operation_id)
                if existing is not None:
                    if _binding(existing) != _binding(record):
                        raise OptimisticConcurrencyError("operation_id_binding_conflict")
                    self._connection.commit()
                    return existing
                self._insert(record)
                self._connection.commit()
                return record
            except Exception:
                self._connection.rollback()
                raise

    def authorize(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        authorization_envelope_id: str,
    ) -> SideEffectRecord:
        if not authorization_envelope_id:
            raise ValueError("authorization_envelope_id_required")
        return self._mutate(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            to_status="authorized",
            authorization_envelope_id=authorization_envelope_id,
        )

    def claim(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
    ) -> SideEffectClaim:
        if not attempt_id:
            raise ValueError("attempt_id_required")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read_required(operation_id)
                if current.status == "completed":
                    self._connection.commit()
                    return SideEffectClaim(current, False, "already_completed")
                if (
                    current.status == "started"
                    and current.fencing_token == fencing_token
                    and current.attempt_id == attempt_id
                ):
                    self._connection.commit()
                    return SideEffectClaim(current, False, "already_claimed")
                updated = _transition(
                    current,
                    expected_revision=expected_revision,
                    fencing_token=fencing_token,
                    to_status="started",
                    attempt_id=attempt_id,
                    require_exact_fence=True,
                )
                self._update(updated, expected_previous_revision=current.revision)
                self._connection.commit()
                return SideEffectClaim(updated, True, "acquired")
            except Exception:
                self._connection.rollback()
                raise

    def complete(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        result_ref: str,
    ) -> SideEffectRecord:
        return self._finish(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            to_status="completed",
            result_ref=result_ref,
        )

    def fail(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str,
    ) -> SideEffectRecord:
        return self._finish(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            to_status="failed",
            failure_code=str(failure_code or "operation_failed"),
        )

    def mark_uncertain(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str = "outcome_unknown",
    ) -> SideEffectRecord:
        return self._finish(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            to_status="uncertain",
            failure_code=failure_code,
        )

    def reconcile_uncertain(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        failure_code: str = "owner_lost",
    ) -> SideEffectRecord:
        return self._mutate(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            to_status="uncertain",
            failure_code=failure_code,
        )

    def compensate(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        result_ref: str,
    ) -> SideEffectRecord:
        return self._mutate(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            to_status="compensated",
            result_ref=result_ref,
        )

    def get(self, *, tenant_id: str, operation_id: str) -> SideEffectRecord | None:
        with self._lock:
            record = self._read(str(operation_id))
        return record if record and record.tenant_id == str(tenant_id) else None

    def _finish(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        to_status: str,
        result_ref: str = "",
        failure_code: str = "",
    ) -> SideEffectRecord:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read_required(operation_id)
                if current.attempt_id != attempt_id:
                    raise FencingTokenError("side_effect_attempt_mismatch")
                updated = _transition(
                    current,
                    expected_revision=expected_revision,
                    fencing_token=fencing_token,
                    to_status=to_status,
                    attempt_id=attempt_id,
                    result_ref=result_ref,
                    failure_code=failure_code,
                    require_exact_fence=True,
                )
                self._update(updated, expected_previous_revision=current.revision)
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def _mutate(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        to_status: str,
        authorization_envelope_id: str = "",
        result_ref: str = "",
        failure_code: str = "",
    ) -> SideEffectRecord:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read_required(operation_id)
                updated = _transition(
                    current,
                    expected_revision=expected_revision,
                    fencing_token=fencing_token,
                    to_status=to_status,
                    authorization_envelope_id=authorization_envelope_id,
                    result_ref=result_ref,
                    failure_code=failure_code,
                )
                self._update(updated, expected_previous_revision=current.revision)
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def _read(self, operation_id: str) -> SideEffectRecord | None:
        row = self._connection.execute(
            "SELECT record_json FROM workflow_side_effect_ledger WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        return SideEffectRecord.from_mapping(json.loads(str(row["record_json"]))) if row else None

    def _read_required(self, operation_id: str) -> SideEffectRecord:
        record = self._read(operation_id)
        if record is None:
            raise KeyError("side_effect_operation_not_found")
        return record

    def _insert(self, record: SideEffectRecord) -> None:
        self._connection.execute(
            """
            INSERT INTO workflow_side_effect_ledger
            (operation_id, tenant_id, run_id, step_id, status, revision, fencing_token, record_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.operation_id,
                record.tenant_id,
                record.run_id,
                record.step_id,
                record.status,
                record.revision,
                record.fencing_token,
                canonical_json(record.to_dict()),
            ),
        )

    def _update(self, record: SideEffectRecord, *, expected_previous_revision: int) -> None:
        cursor = self._connection.execute(
            """
            UPDATE workflow_side_effect_ledger
            SET status = ?, revision = ?, fencing_token = ?, record_json = ?
            WHERE operation_id = ? AND revision = ?
            """,
            (
                record.status,
                record.revision,
                record.fencing_token,
                canonical_json(record.to_dict()),
                record.operation_id,
                expected_previous_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise OptimisticConcurrencyError("side_effect_compare_and_set_failed")


def _new_record(
    *,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    step_id: str,
    declared_operation: str,
    side_effect_class: str,
) -> SideEffectRecord:
    record = SideEffectRecord(
        operation_id=operation_id_for(
            tenant_id=tenant_id,
            run_id=run_id,
            step_id=step_id,
            declared_operation=declared_operation,
        ),
        tenant_id=str(tenant_id).strip(),
        workflow_id=str(workflow_id).strip(),
        run_id=str(run_id).strip(),
        step_id=str(step_id).strip(),
        declared_operation=str(declared_operation).strip(),
        side_effect_class=str(side_effect_class).strip(),
        updated_at=time.time(),
    )
    record.assert_valid()
    return record


def _binding(record: SideEffectRecord) -> tuple[str, ...]:
    return (
        record.tenant_id,
        record.workflow_id,
        record.run_id,
        record.step_id,
        record.declared_operation,
        record.side_effect_class,
    )


def _transition(
    current: SideEffectRecord,
    *,
    expected_revision: int,
    fencing_token: int,
    to_status: str,
    attempt_id: str | object = "",
    authorization_envelope_id: str | object = "",
    result_ref: str | object = "",
    failure_code: str | object = "",
    require_exact_fence: bool = False,
) -> SideEffectRecord:
    if current.revision != int(expected_revision):
        raise OptimisticConcurrencyError(
            f"side_effect_revision_conflict:expected={expected_revision}:actual={current.revision}"
        )
    fence = int(fencing_token)
    if fence < current.fencing_token or (require_exact_fence and fence != current.fencing_token):
        raise FencingTokenError("side_effect_fencing_token_stale")
    if str(to_status) not in _TRANSITIONS.get(current.status, frozenset()):
        raise InvalidTransitionError(f"side_effect_transition_invalid:{current.status}:{to_status}")
    updated = replace(
        current,
        status=str(to_status),
        revision=current.revision + 1,
        fencing_token=fence,
        attempt_id=str(attempt_id or current.attempt_id),
        authorization_envelope_id=str(authorization_envelope_id or current.authorization_envelope_id),
        result_ref=str(result_ref or current.result_ref),
        failure_code=str(failure_code or ""),
        updated_at=time.time(),
    )
    updated.assert_valid()
    return updated
