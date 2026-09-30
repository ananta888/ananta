"""SQLite reference adapter for the side-effect ledger and its authorizations."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.errors import (
    FencingTokenError,
    OptimisticConcurrencyError,
)
from agent.services.workflow_runtime.side_effect_authorization import (
    _MAX_OPERATION_RECEIPTS,
    WorkflowTransitionSideEffectAuthorizationIntent,
    WorkflowTransitionSideEffectAuthorizationObservation,
    WorkflowTransitionSideEffectAuthorizationReceipt,
    _transition_authorization_commit_values,
    _transition_authorization_observation,
    assert_workflow_transition_side_effect_authorization_observation_digest,
)
from agent.services.workflow_runtime.side_effect_records import (
    SideEffectClaim,
    SideEffectRecord,
    _assert_side_effect_row_projection,
    _binding,
    _new_record,
    _transition,
)


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
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_transition_side_effect_authorizations (
                receipt_id TEXT PRIMARY KEY,
                transition_id TEXT NOT NULL,
                effect_id TEXT NOT NULL UNIQUE,
                operation_id TEXT NOT NULL,
                operation_fence_id TEXT NOT NULL UNIQUE,
                tenant_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                runtime_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                operation_intent_digest TEXT NOT NULL,
                authorization_envelope_id TEXT NOT NULL,
                authorization_envelope_digest TEXT NOT NULL,
                ownership_attempt_id TEXT NOT NULL,
                ownership_fencing_token INTEGER NOT NULL,
                creator_claim_generation INTEGER NOT NULL,
                authorized_ledger_revision INTEGER NOT NULL,
                planned_at REAL NOT NULL,
                authorized_at REAL NOT NULL,
                receipt_digest TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                UNIQUE (operation_id, authorized_ledger_revision),
                CHECK (ownership_fencing_token > 0),
                CHECK (creator_claim_generation > 0),
                CHECK (authorized_ledger_revision > 1)
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_transition_side_effect_auth_operation "
            "ON workflow_transition_side_effect_authorizations (operation_id)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_transition_side_effect_auth_tenant_run "
            "ON workflow_transition_side_effect_authorizations (tenant_id, run_id)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_transition_side_effect_auth_transition "
            "ON workflow_transition_side_effect_authorizations (transition_id)"
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

    def observe_transition_authorization(
        self,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
    ) -> WorkflowTransitionSideEffectAuthorizationObservation:
        with self._lock:
            self._connection.execute("BEGIN")
            try:
                observation = self._read_transition_authorization_observation(intent)
                self._connection.commit()
                return observation
            except BaseException:
                self._connection.rollback()
                raise

    def authorize_transition_effect(
        self,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
        *,
        expected_observation_digest: str,
    ) -> WorkflowTransitionSideEffectAuthorizationReceipt:
        expected_digest = assert_workflow_transition_side_effect_authorization_observation_digest(
            expected_observation_digest
        )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                observation = self._read_transition_authorization_observation(intent)
                if observation.receipt is not None:
                    self._connection.commit()
                    return observation.receipt
                if observation.observation_digest != expected_digest:
                    raise OptimisticConcurrencyError(
                        "workflow_transition_side_effect_authorization_observation_conflict"
                    )
                planned, authorized, receipt = _transition_authorization_commit_values(
                    intent,
                    current=observation.ledger_record,
                    prior_receipts=observation.operation_receipts,
                )
                if observation.ledger_record is None:
                    self._insert(planned)
                self._transition_authorization_fault("after_plan", planned)
                self._update(
                    authorized,
                    expected_previous_revision=planned.revision,
                )
                self._transition_authorization_fault("after_authorize", authorized)
                self._insert_transition_authorization_receipt(receipt)
                self._transition_authorization_fault("before_commit", receipt)
                self._connection.commit()
                return receipt
            except BaseException:
                self._connection.rollback()
                raise

    def _read_transition_authorization_observation(
        self,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
    ) -> WorkflowTransitionSideEffectAuthorizationObservation:
        ledger_record = self._read_exact(intent.operation_id)
        rows = self._connection.execute(
            """
            SELECT * FROM workflow_transition_side_effect_authorizations
            WHERE operation_id = ? OR receipt_id = ? OR effect_id = ? OR operation_fence_id = ?
            ORDER BY authorized_ledger_revision, receipt_id
            LIMIT ?
            """,
            (
                intent.operation_id,
                intent.receipt_id,
                intent.effect_id,
                intent.operation_fence_id,
                _MAX_OPERATION_RECEIPTS + 1,
            ),
        ).fetchall()
        if len(rows) > _MAX_OPERATION_RECEIPTS:
            raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_history_limit")
        receipts = tuple(_sqlite_transition_authorization_receipt(row) for row in rows)
        return _transition_authorization_observation(
            intent,
            ledger_record=ledger_record,
            receipts=receipts,
        )

    def _insert_transition_authorization_receipt(
        self,
        receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO workflow_transition_side_effect_authorizations
            (receipt_id, transition_id, effect_id, operation_id, operation_fence_id,
             tenant_id, workflow_id, run_id, runtime_id, step_id,
             operation_intent_digest, authorization_envelope_id,
             authorization_envelope_digest, ownership_attempt_id,
             ownership_fencing_token, creator_claim_generation,
             authorized_ledger_revision, planned_at, authorized_at,
             receipt_digest, receipt_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _transition_authorization_receipt_row_values(receipt),
        )

    def _transition_authorization_fault(self, stage: str, value: object) -> None:
        del stage, value

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

    def _read_exact(self, operation_id: str) -> SideEffectRecord | None:
        row = self._connection.execute(
            "SELECT operation_id, tenant_id, run_id, step_id, status, revision, "
            "fencing_token, record_json FROM workflow_side_effect_ledger "
            "WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            raw = json.loads(str(row["record_json"]))
            record = SideEffectRecord.from_exact_mapping(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OptimisticConcurrencyError("workflow_transition_side_effect_ledger_record_invalid") from exc
        _assert_side_effect_row_projection(
            record,
            operation_id=row["operation_id"],
            tenant_id=row["tenant_id"],
            run_id=row["run_id"],
            step_id=row["step_id"],
            status=row["status"],
            revision=row["revision"],
            fencing_token=row["fencing_token"],
        )
        return record

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


def _transition_authorization_receipt_row_values(
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
) -> tuple[object, ...]:
    return (
        receipt.receipt_id,
        receipt.transition_id,
        receipt.effect_id,
        receipt.operation_id,
        receipt.operation_fence_id,
        receipt.tenant_id,
        receipt.workflow_id,
        receipt.run_id,
        receipt.runtime_id,
        receipt.step_id,
        receipt.operation_intent_digest,
        receipt.authorization_envelope_id,
        receipt.authorization_envelope_digest,
        receipt.ownership_attempt_id,
        receipt.ownership_fencing_token,
        receipt.creator_claim_generation,
        receipt.authorized_ledger_revision,
        receipt.planned_at,
        receipt.authorized_at,
        receipt.receipt_digest,
        canonical_json(receipt.to_dict()),
    )


def _sqlite_transition_authorization_receipt(
    row: sqlite3.Row,
) -> WorkflowTransitionSideEffectAuthorizationReceipt:
    try:
        raw = json.loads(str(row["receipt_json"]))
        receipt = WorkflowTransitionSideEffectAuthorizationReceipt.from_mapping(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_receipt_invalid") from exc
    projection = {
        "receipt_id": row["receipt_id"],
        "transition_id": row["transition_id"],
        "effect_id": row["effect_id"],
        "operation_id": row["operation_id"],
        "operation_fence_id": row["operation_fence_id"],
        "tenant_id": row["tenant_id"],
        "workflow_id": row["workflow_id"],
        "run_id": row["run_id"],
        "runtime_id": row["runtime_id"],
        "step_id": row["step_id"],
        "operation_intent_digest": row["operation_intent_digest"],
        "authorization_envelope_id": row["authorization_envelope_id"],
        "authorization_envelope_digest": row["authorization_envelope_digest"],
        "ownership_attempt_id": row["ownership_attempt_id"],
        "ownership_fencing_token": row["ownership_fencing_token"],
        "creator_claim_generation": row["creator_claim_generation"],
        "authorized_ledger_revision": row["authorized_ledger_revision"],
        "planned_at": row["planned_at"],
        "authorized_at": row["authorized_at"],
        "receipt_digest": row["receipt_digest"],
    }
    if any(getattr(receipt, name) != value for name, value in projection.items()):
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_receipt_projection_conflict")
    return receipt
