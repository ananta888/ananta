"""SQLite-backed execution ownership store with transition reservations."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.errors import FencingTokenError, InvalidTransitionError, OptimisticConcurrencyError
from agent.services.workflow_runtime.ownership_records import (
    ExecutionOwnership,
    OwnershipClaim,
    RetryBudgetSnapshot,
    _assert_expected_revision,
    _assert_owner_from_values,
    _heartbeat,
    _timestamp,
    _validate_lease,
)
from agent.services.workflow_runtime.ownership_sqlite_schema import (
    _direct_exact_current,
    _direct_exact_history,
    _direct_transition_reservation_receipt,
    _direct_transition_reservation_receipt_values,
    _preflight_direct_ownership_schema,
)
from agent.services.workflow_runtime.ownership_transition_errors import (
    WorkflowTransitionOwnershipReservationConflict,
    WorkflowTransitionOwnershipReservationStale,
    WorkflowTransitionOwnershipReservationUnavailable,
)
from agent.services.workflow_runtime.ownership_transition_projection import (
    _transition_ownership_evidence,
    _transition_ownership_observation,
    _transition_ownership_reservation_values,
)
from agent.services.workflow_runtime.ownership_transition_reservation import (
    WorkflowTransitionOwnershipReservationEvidence,
    WorkflowTransitionOwnershipReservationIntent,
    WorkflowTransitionOwnershipReservationObservation,
    WorkflowTransitionOwnershipReservationReceipt,
    WorkflowTransitionOwnershipRetryConsumption,
)
from agent.services.workflow_runtime.ownership_values import _OWNERSHIP_MAX_LEGACY_REVISION, _OWNERSHIP_MAX_RETRIES
from ananta_contracts.hub_task_gateway import RETRY_CATEGORIES


class SQLiteExecutionOwnershipStore:
    def acknowledge_result_fenced(self, *, lease, **values):
        return self.acknowledge_result(**values, _control_lease=lease)

    def fail_attempt_fenced(self, *, lease, **values):
        return self.fail_attempt(**values, _control_lease=lease)

    def _validate_control_lease(self, values):
        from agent.services.workflow_runtime.lease_fencing import ownership_lease_recipient, validate_sqlite_lease

        lease = values.get("_control_lease")
        if lease is not None:
            validate_sqlite_lease(self._connection, lease, ownership_lease_recipient(values, lease))

    """SQLite lease store; ownership and combined retry budget mutate atomically."""

    def __init__(self, database: str | Path):
        self._connection = sqlite3.connect(str(database), timeout=30, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._lock = threading.RLock()
        _preflight_direct_ownership_schema(self._connection)
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflow_execution_ownership (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                fencing_token INTEGER NOT NULL,
                ownership_json TEXT NOT NULL,
                PRIMARY KEY (tenant_id, run_id, step_id)
            );
            CREATE TABLE IF NOT EXISTS workflow_execution_attempt_history (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                attempt_id TEXT NOT NULL,
                ownership_json TEXT NOT NULL,
                PRIMARY KEY (tenant_id, run_id, step_id, revision)
            );
            CREATE TABLE IF NOT EXISTS workflow_retry_budgets (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                used INTEGER NOT NULL,
                maximum INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, run_id)
            );
            CREATE TABLE IF NOT EXISTS workflow_retry_consumptions (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                retry_id TEXT NOT NULL,
                category TEXT NOT NULL,
                PRIMARY KEY (tenant_id, run_id, retry_id)
            );
            CREATE TABLE IF NOT EXISTS workflow_transition_ownership_reservations (
                receipt_id TEXT NOT NULL PRIMARY KEY,
                transition_id TEXT NOT NULL,
                effect_id TEXT NOT NULL,
                operation_fence_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                runtime_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                ownership_intent_digest TEXT NOT NULL,
                acquisition_record_digest TEXT NOT NULL,
                receipt_digest TEXT NOT NULL,
                creator_claim_generation INTEGER NOT NULL,
                acquired_revision INTEGER NOT NULL,
                acquired_fencing_token INTEGER NOT NULL,
                maximum_retries INTEGER NOT NULL,
                retry_consumed INTEGER NOT NULL,
                planned_at REAL NOT NULL,
                reserved_at REAL NOT NULL,
                lease_expires_at REAL NOT NULL,
                receipt_json TEXT NOT NULL,
                CONSTRAINT uq_workflow_transition_ownership_res_effect UNIQUE (effect_id),
                CONSTRAINT uq_workflow_transition_ownership_res_fence UNIQUE (operation_fence_id),
                CONSTRAINT uq_workflow_transition_ownership_res_attempt UNIQUE (attempt_id),
                CONSTRAINT uq_workflow_transition_ownership_res_revision
                    UNIQUE (tenant_id, run_id, step_id, acquired_revision),
                CONSTRAINT uq_workflow_transition_ownership_res_current_fence
                    UNIQUE (tenant_id, run_id, step_id, acquired_fencing_token),
                CONSTRAINT ck_workflow_transition_ownership_res_valid CHECK (
                    creator_claim_generation > 0
                    AND acquired_revision > 0
                    AND acquired_revision <= 2147483647
                    AND acquired_fencing_token > 0
                    AND acquired_fencing_token <= 2147483647
                    AND maximum_retries >= 0
                    AND maximum_retries <= 2147483647
                    AND retry_consumed IN (0, 1)
                    AND planned_at > 0
                    AND reserved_at >= planned_at
                    AND lease_expires_at > reserved_at
                )
            );
            CREATE INDEX IF NOT EXISTS ix_workflow_transition_ownership_res_transition
                ON workflow_transition_ownership_reservations (transition_id);
            CREATE INDEX IF NOT EXISTS ix_workflow_transition_ownership_res_tenant_run
                ON workflow_transition_ownership_reservations (tenant_id, run_id);
            CREATE INDEX IF NOT EXISTS ix_workflow_transition_ownership_res_scope
                ON workflow_transition_ownership_reservations (tenant_id, run_id, step_id);
            CREATE INDEX IF NOT EXISTS ix_workflow_transition_ownership_res_owner
                ON workflow_transition_ownership_reservations (owner_id);
            """
        )
        _preflight_direct_ownership_schema(self._connection)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

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
    ) -> OwnershipClaim:
        timestamp = _validate_lease(lease_seconds, now)
        key = (str(tenant_id), str(run_id), str(step_id))
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read(*key)
                if current is not None and current.workflow_id != str(workflow_id):
                    raise OptimisticConcurrencyError("execution_ownership_workflow_binding_conflict")
                if current and current.status == "completed":
                    self._connection.commit()
                    return OwnershipClaim(current, False, "already_completed")
                if current and current.status == "active" and current.lease_expires_at > timestamp:
                    reason = "already_owned" if current.owner_id == str(owner_id) else "lease_held"
                    self._connection.commit()
                    return OwnershipClaim(current, False, reason)
                attempt_id = f"att-{uuid.uuid4().hex}"
                if current is not None:
                    self._consume_retry_in_transaction(
                        tenant_id=str(tenant_id),
                        run_id=str(run_id),
                        retry_id=attempt_id,
                        category="hub_task",
                        maximum=int(maximum_retries),
                    )
                ownership = ExecutionOwnership(
                    tenant_id=str(tenant_id),
                    workflow_id=str(workflow_id),
                    run_id=str(run_id),
                    step_id=str(step_id),
                    attempt_id=attempt_id,
                    owner_id=str(owner_id),
                    fencing_token=(current.fencing_token + 1) if current else 1,
                    revision=(current.revision + 1) if current else 1,
                    status="active",
                    lease_expires_at=timestamp + float(lease_seconds),
                    last_heartbeat_at=timestamp,
                )
                self._write(ownership, expected_revision=current.revision if current else 0)
                self._connection.commit()
                return OwnershipClaim(ownership, True, "acquired" if current is None else "recovered")
            except Exception:
                self._connection.rollback()
                raise

    def observe_transition_reservation(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        claim_generation: int,
    ) -> WorkflowTransitionOwnershipReservationObservation:
        try:
            return self._observe_transition_reservation_once(
                intent,
                claim_generation=claim_generation,
            )
        except WorkflowTransitionOwnershipReservationConflict:
            raise
        except sqlite3.Error as exc:
            raise WorkflowTransitionOwnershipReservationUnavailable(
                "workflow_transition_ownership_read_unavailable"
            ) from exc

    def read_transition_reservation_history(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
    ) -> WorkflowTransitionOwnershipReservationEvidence:
        try:
            return self._read_transition_reservation_history_once(intent)
        except WorkflowTransitionOwnershipReservationConflict:
            raise
        except sqlite3.Error as exc:
            raise WorkflowTransitionOwnershipReservationUnavailable(
                "workflow_transition_ownership_history_unavailable"
            ) from exc

    def reserve_transition_effect(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        creator_claim_generation: int,
        expected_observation_digest: str,
        reserved_at: float,
    ) -> WorkflowTransitionOwnershipReservationReceipt:
        try:
            return self._reserve_transition_effect_once(
                intent,
                creator_claim_generation=creator_claim_generation,
                expected_observation_digest=expected_observation_digest,
                reserved_at=reserved_at,
            )
        except WorkflowTransitionOwnershipReservationStale:
            raise
        except WorkflowTransitionOwnershipReservationConflict:
            raise
        except (sqlite3.IntegrityError, OptimisticConcurrencyError) as exc:
            try:
                evidence = self._read_transition_reservation_history_once(intent)
                if evidence.receipt is not None and (
                    evidence.receipt.creator_claim_generation <= creator_claim_generation
                ):
                    return evidence.receipt
                observation = self._observe_transition_reservation_once(
                    intent,
                    claim_generation=creator_claim_generation,
                )
            except WorkflowTransitionOwnershipReservationConflict:
                raise
            except sqlite3.Error as read_exc:
                raise WorkflowTransitionOwnershipReservationUnavailable(
                    "workflow_transition_ownership_commit_unavailable"
                ) from read_exc
            if observation.receipt is not None:
                return observation.receipt
            raise WorkflowTransitionOwnershipReservationStale("workflow_transition_ownership_commit_stale") from exc
        except sqlite3.Error as exc:
            try:
                evidence = self._read_transition_reservation_history_once(intent)
            except WorkflowTransitionOwnershipReservationConflict:
                raise
            except sqlite3.Error as read_exc:
                raise WorkflowTransitionOwnershipReservationUnavailable(
                    "workflow_transition_ownership_commit_unavailable"
                ) from read_exc
            if evidence.receipt is not None and evidence.receipt.creator_claim_generation <= creator_claim_generation:
                return evidence.receipt
            raise WorkflowTransitionOwnershipReservationUnavailable(
                "workflow_transition_ownership_commit_unavailable"
            ) from exc

    def _observe_transition_reservation_once(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        claim_generation: int,
    ) -> WorkflowTransitionOwnershipReservationObservation:
        with self._lock:
            self._connection.execute("BEGIN")
            try:
                observation = self._read_transition_reservation_observation(
                    intent,
                    claim_generation=claim_generation,
                )
                self._connection.commit()
                return observation
            except BaseException:
                self._connection.rollback()
                raise

    def _read_transition_reservation_history_once(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
    ) -> WorkflowTransitionOwnershipReservationEvidence:
        with self._lock:
            self._connection.execute("BEGIN")
            try:
                evidence = self._read_transition_reservation_history_in_transaction(intent)
                self._connection.commit()
                return evidence
            except BaseException:
                self._connection.rollback()
                raise

    def _read_transition_reservation_history_in_transaction(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
    ) -> WorkflowTransitionOwnershipReservationEvidence:
        if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
        rows = self._connection.execute(
            """
            SELECT * FROM workflow_transition_ownership_reservations
            WHERE receipt_id = ? OR effect_id = ?
               OR operation_fence_id = ? OR attempt_id = ?
            ORDER BY receipt_id
            """,
            (
                intent.receipt_id,
                intent.effect_id,
                intent.operation_fence_id,
                intent.attempt_id,
            ),
        ).fetchall()
        receipts = tuple(_direct_transition_reservation_receipt(row) for row in rows)
        distinct = {value.receipt_digest: value for value in receipts}
        if len(distinct) == 1:
            receipt = next(iter(distinct.values()))
            revisions = [receipt.acquired_revision]
            if receipt.prior_ownership is not None:
                revisions.append(receipt.prior_ownership.revision)
            placeholders = ",".join("?" for _ in revisions)
            history_rows = self._connection.execute(
                f"""
                SELECT tenant_id, run_id, step_id, revision, attempt_id, ownership_json
                FROM workflow_execution_attempt_history
                WHERE tenant_id = ? AND run_id = ? AND step_id = ?
                  AND revision IN ({placeholders})
                """,
                (
                    intent.tenant_id,
                    intent.run_id,
                    intent.step_id,
                    *revisions,
                ),
            ).fetchall()
        else:
            history_rows = self._connection.execute(
                """
                SELECT tenant_id, run_id, step_id, revision, attempt_id, ownership_json
                FROM workflow_execution_attempt_history
                WHERE tenant_id = ? AND run_id = ? AND step_id = ? AND attempt_id = ?
                LIMIT 2
                """,
                (
                    intent.tenant_id,
                    intent.run_id,
                    intent.step_id,
                    intent.attempt_id,
                ),
            ).fetchall()
        retry_row = self._connection.execute(
            """
            SELECT tenant_id, run_id, retry_id, category
            FROM workflow_retry_consumptions
            WHERE tenant_id = ? AND run_id = ? AND retry_id = ?
            """,
            (intent.tenant_id, intent.run_id, intent.retry_id),
        ).fetchone()
        return _transition_ownership_evidence(
            intent,
            history=tuple(_direct_exact_history(row) for row in history_rows),
            retry_consumption=(
                None if retry_row is None else WorkflowTransitionOwnershipRetryConsumption.from_mapping(dict(retry_row))
            ),
            receipts=receipts,
        )

    def _reserve_transition_effect_once(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        creator_claim_generation: int,
        expected_observation_digest: str,
        reserved_at: float,
    ) -> WorkflowTransitionOwnershipReservationReceipt:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                evidence = self._read_transition_reservation_history_in_transaction(intent)
                if evidence.receipt is not None:
                    if evidence.receipt.creator_claim_generation > creator_claim_generation:
                        raise WorkflowTransitionOwnershipReservationConflict(
                            "workflow_transition_ownership_receipt_generation_conflict"
                        )
                    self._connection.commit()
                    return evidence.receipt
                observation = self._read_transition_reservation_observation(
                    intent,
                    claim_generation=creator_claim_generation,
                )
                acquired, consumption, budget, receipt = _transition_ownership_reservation_values(
                    observation,
                    creator_claim_generation=creator_claim_generation,
                    expected_observation_digest=expected_observation_digest,
                    reserved_at=reserved_at,
                )
                if observation.receipt is not None:
                    self._connection.commit()
                    return observation.receipt
                self._transition_reservation_fault("before_retry", consumption)
                if consumption is not None:
                    self._connection.execute(
                        """
                        INSERT INTO workflow_retry_consumptions
                        (tenant_id, run_id, retry_id, category) VALUES (?, ?, ?, ?)
                        """,
                        (
                            consumption.tenant_id,
                            consumption.run_id,
                            consumption.retry_id,
                            consumption.category,
                        ),
                    )
                    budget_row = self._connection.execute(
                        """
                        UPDATE workflow_retry_budgets SET used = ?
                        WHERE tenant_id = ? AND run_id = ? AND used = ? AND maximum = ?
                        """,
                        (
                            budget.used,
                            intent.tenant_id,
                            intent.run_id,
                            observation.retry_budget.used,
                            intent.maximum_retries,
                        ),
                    )
                    if budget_row.rowcount == 0:
                        if observation.retry_budget.used != 0:
                            raise WorkflowTransitionOwnershipReservationStale(
                                "workflow_transition_ownership_retry_budget_cas_failed"
                            )
                        self._connection.execute(
                            """
                            INSERT INTO workflow_retry_budgets
                            (tenant_id, run_id, used, maximum) VALUES (?, ?, ?, ?)
                            """,
                            (
                                intent.tenant_id,
                                intent.run_id,
                                budget.used,
                                budget.maximum,
                            ),
                        )
                self._transition_reservation_fault("after_retry", consumption)
                self._write(acquired, expected_revision=observation.current.revision if observation.current else 0)
                self._transition_reservation_fault("after_current", acquired)
                self._transition_reservation_fault("after_history", acquired)
                self._insert_transition_reservation_receipt(receipt)
                self._transition_reservation_fault("after_receipt", receipt)
                self._transition_reservation_fault("before_commit", receipt)
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
            self._transition_reservation_fault("after_commit", receipt)
            return receipt

    def _read_transition_reservation_observation(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        claim_generation: int,
    ) -> WorkflowTransitionOwnershipReservationObservation:
        if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
        current = self._read_exact_ownership(
            intent.tenant_id,
            intent.run_id,
            intent.step_id,
        )
        receipts = self._read_transition_reservation_receipts(intent, current=current)
        anchor_revisions = {
            revision
            for receipt in receipts
            for revision in (
                receipt.acquired_revision,
                receipt.prior_ownership.revision if receipt.prior_ownership is not None else 0,
            )
            if revision > 0
        }
        if current is not None:
            anchor_revisions.add(current.revision)
        clauses = ["attempt_id = ?"]
        values: list[object] = [
            intent.tenant_id,
            intent.run_id,
            intent.step_id,
            intent.attempt_id,
        ]
        if anchor_revisions:
            clauses.append(f"revision IN ({','.join('?' for _ in anchor_revisions)})")
            values.extend(sorted(anchor_revisions))
        if current is None:
            clauses.append(
                "revision = (SELECT MAX(revision) FROM workflow_execution_attempt_history "
                "WHERE tenant_id = ? AND run_id = ? AND step_id = ?)"
            )
            values.extend([intent.tenant_id, intent.run_id, intent.step_id])
        history_rows = self._connection.execute(
            f"""
            SELECT tenant_id, run_id, step_id, revision, attempt_id, ownership_json
            FROM workflow_execution_attempt_history
            WHERE tenant_id = ? AND run_id = ? AND step_id = ?
              AND ({" OR ".join(clauses)})
            ORDER BY revision ASC
            """,
            tuple(values),
        ).fetchall()
        history = tuple(_direct_exact_history(row) for row in history_rows)
        retry_row = self._connection.execute(
            """
            SELECT tenant_id, run_id, retry_id, category
            FROM workflow_retry_consumptions
            WHERE tenant_id = ? AND run_id = ? AND retry_id = ?
            """,
            (intent.tenant_id, intent.run_id, intent.retry_id),
        ).fetchone()
        retry_consumption = (
            None if retry_row is None else WorkflowTransitionOwnershipRetryConsumption.from_mapping(dict(retry_row))
        )
        budget_row = self._connection.execute(
            """
            SELECT used, maximum FROM workflow_retry_budgets
            WHERE tenant_id = ? AND run_id = ?
            """,
            (intent.tenant_id, intent.run_id),
        ).fetchone()
        if budget_row is not None and (
            type(budget_row["used"]) is not int
            or type(budget_row["maximum"]) is not int
            or budget_row["used"] < 0
            or budget_row["maximum"] < 0
            or budget_row["maximum"] > _OWNERSHIP_MAX_RETRIES
        ):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_retry_budget_projection_conflict"
            )
        budget = RetryBudgetSnapshot(
            intent.tenant_id,
            intent.run_id,
            used=budget_row["used"] if budget_row else 0,
            maximum=budget_row["maximum"] if budget_row else intent.maximum_retries,
        )
        return _transition_ownership_observation(
            intent,
            claim_generation=claim_generation,
            current=current,
            history=history,
            retry_consumption=retry_consumption,
            retry_budget=budget,
            receipts=receipts,
        )

    def _read_transition_reservation_receipts(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        current: ExecutionOwnership | None,
    ) -> tuple[WorkflowTransitionOwnershipReservationReceipt, ...]:
        alias_values: list[object] = [
            intent.receipt_id,
            intent.effect_id,
            intent.operation_fence_id,
            intent.attempt_id,
        ]
        sql = """
            SELECT * FROM workflow_transition_ownership_reservations
            WHERE receipt_id = ? OR effect_id = ? OR operation_fence_id = ? OR attempt_id = ?
        """
        if current is not None:
            sql += """
                OR attempt_id = ?
                OR (
                    tenant_id = ? AND run_id = ? AND step_id = ?
                    AND (acquired_revision = ? OR acquired_fencing_token = ?)
                )
            """
            alias_values.extend(
                [
                    current.attempt_id,
                    current.tenant_id,
                    current.run_id,
                    current.step_id,
                    current.revision,
                    current.fencing_token,
                ]
            )
        if current is None:
            sql += """
                OR (tenant_id = ? AND run_id = ? AND step_id = ?)
            """
            alias_values.extend([intent.tenant_id, intent.run_id, intent.step_id])
        elif (
            current.revision < _OWNERSHIP_MAX_LEGACY_REVISION and current.fencing_token < _OWNERSHIP_MAX_LEGACY_REVISION
        ):
            next_revision = current.revision + 1
            next_fencing_token = current.fencing_token + 1
            sql += """
                OR (
                    tenant_id = ? AND run_id = ? AND step_id = ?
                    AND (acquired_revision >= ? OR acquired_fencing_token >= ?)
                )
            """
            alias_values.extend(
                [
                    intent.tenant_id,
                    intent.run_id,
                    intent.step_id,
                    next_revision,
                    next_fencing_token,
                ]
            )
        rows = self._connection.execute(sql + " ORDER BY receipt_id LIMIT 17", tuple(alias_values)).fetchall()
        return tuple(_direct_transition_reservation_receipt(row) for row in rows)

    def _read_exact_ownership(self, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None:
        row = self._connection.execute(
            """
            SELECT tenant_id, run_id, step_id, revision, fencing_token, ownership_json
            FROM workflow_execution_ownership
            WHERE tenant_id = ? AND run_id = ? AND step_id = ?
            """,
            (tenant_id, run_id, step_id),
        ).fetchone()
        return None if row is None else _direct_exact_current(row)

    def _insert_transition_reservation_receipt(self, receipt: WorkflowTransitionOwnershipReservationReceipt) -> None:
        values = _direct_transition_reservation_receipt_values(receipt)
        self._connection.execute(
            """
            INSERT INTO workflow_transition_ownership_reservations (
                receipt_id, transition_id, effect_id, operation_fence_id,
                attempt_id, owner_id, tenant_id, workflow_id, run_id, runtime_id,
                step_id, ownership_intent_digest, acquisition_record_digest,
                receipt_digest, creator_claim_generation, acquired_revision,
                acquired_fencing_token, maximum_retries, retry_consumed,
                planned_at, reserved_at, lease_expires_at, receipt_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            values,
        )

    def _transition_reservation_fault(self, stage: str, value: object) -> None:
        del stage, value

    def heartbeat(self, **values: Any) -> ExecutionOwnership:
        timestamp = _validate_lease(float(values["lease_seconds"]), values.get("now"))
        return self._mutate_owned(
            values,
            lambda current: _heartbeat(current, values=values, timestamp=timestamp),
        )

    def acknowledge_result(self, **values: Any) -> ExecutionOwnership:
        result_ack_key = str(values.get("result_ack_key") or "")
        if not result_ack_key:
            raise ValueError("result_ack_key_required")
        timestamp = _timestamp(values.get("now"))
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._validate_control_lease(values)
                current = self._read_required(values)
                _assert_owner_from_values(current, values)
                if current.status == "completed" and current.result_ack_key == result_ack_key:
                    self._connection.commit()
                    return current
                _assert_expected_revision(current, int(values["expected_revision"]))
                if current.status != "active" or current.lease_expires_at <= timestamp:
                    raise FencingTokenError("result_owner_not_active")
                updated = replace(
                    current,
                    revision=current.revision + 1,
                    status="completed",
                    result_ack_key=result_ack_key,
                    lease_expires_at=timestamp,
                )
                self._write(updated, expected_revision=current.revision)
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def fail_attempt(self, *, failure_code: str, dead_letter: bool = False, **values: Any) -> ExecutionOwnership:
        def mutate(current: ExecutionOwnership) -> ExecutionOwnership:
            _assert_expected_revision(current, int(values["expected_revision"]))
            if current.status != "active":
                raise InvalidTransitionError("failure_requires_active_ownership")
            timestamp = _timestamp(values.get("now"))
            if current.lease_expires_at <= timestamp:
                raise FencingTokenError("failure_owner_lease_expired")
            return replace(
                current,
                revision=current.revision + 1,
                status="dead_letter" if dead_letter else "failed",
                failure_code=str(failure_code or "execution_failed"),
                lease_expires_at=timestamp,
            )

        return self._mutate_owned(values, mutate)

    def reconcile_orphan(
        self, *, tenant_id: str, run_id: str, step_id: str, now: float | None = None
    ) -> ExecutionOwnership | None:
        timestamp = float(now if now is not None else time.time())
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read(str(tenant_id), str(run_id), str(step_id))
                if current is None or current.status != "active" or current.lease_expires_at > timestamp:
                    self._connection.commit()
                    return None
                updated = replace(
                    current,
                    revision=current.revision + 1,
                    status="orphaned",
                    failure_code="lease_expired",
                )
                self._write(updated, expected_revision=current.revision)
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def get(self, *, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None:
        with self._lock:
            return self._read(str(tenant_id), str(run_id), str(step_id))

    def consume_retry(
        self,
        *,
        tenant_id: str,
        run_id: str,
        retry_id: str,
        category: str,
        maximum: int,
    ) -> RetryBudgetSnapshot:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                value = self._consume_retry_in_transaction(
                    tenant_id=str(tenant_id),
                    run_id=str(run_id),
                    retry_id=str(retry_id),
                    category=str(category),
                    maximum=int(maximum),
                )
                self._connection.commit()
                return value
            except Exception:
                self._connection.rollback()
                raise

    def get_retry_budget(self, *, tenant_id: str, run_id: str, maximum: int) -> RetryBudgetSnapshot:
        with self._lock:
            row = self._connection.execute(
                "SELECT used, maximum FROM workflow_retry_budgets WHERE tenant_id = ? AND run_id = ?",
                (str(tenant_id), str(run_id)),
            ).fetchone()
        if row is not None and int(row["maximum"]) != int(maximum):
            raise InvalidTransitionError("retry_budget_maximum_mismatch")
        return RetryBudgetSnapshot(
            str(tenant_id), str(run_id), used=int(row["used"] if row else 0), maximum=int(maximum)
        )

    def _consume_retry_in_transaction(
        self, *, tenant_id: str, run_id: str, retry_id: str, category: str, maximum: int
    ) -> RetryBudgetSnapshot:
        if maximum < 0 or not retry_id or category not in RETRY_CATEGORIES:
            raise ValueError("retry_budget_input_invalid")
        duplicate = self._connection.execute(
            """
            SELECT category FROM workflow_retry_consumptions
            WHERE tenant_id = ? AND run_id = ? AND retry_id = ?
            """,
            (tenant_id, run_id, retry_id),
        ).fetchone()
        row = self._connection.execute(
            "SELECT used, maximum FROM workflow_retry_budgets WHERE tenant_id = ? AND run_id = ?",
            (tenant_id, run_id),
        ).fetchone()
        used = int(row["used"] if row else 0)
        if row is not None and int(row["maximum"]) != maximum:
            raise InvalidTransitionError("retry_budget_maximum_mismatch")
        if duplicate and str(duplicate["category"]) != category:
            raise InvalidTransitionError("retry_budget_retry_id_binding_mismatch")
        if duplicate:
            return RetryBudgetSnapshot(tenant_id, run_id, used=used, maximum=maximum)
        if used >= maximum:
            raise InvalidTransitionError("retry_budget_exhausted")
        self._connection.execute(
            """
            INSERT INTO workflow_retry_consumptions (tenant_id, run_id, retry_id, category)
            VALUES (?, ?, ?, ?)
            """,
            (tenant_id, run_id, retry_id, category),
        )
        self._connection.execute(
            """
            INSERT INTO workflow_retry_budgets (tenant_id, run_id, used, maximum) VALUES (?, ?, 1, ?)
            ON CONFLICT (tenant_id, run_id) DO UPDATE SET used = used + 1
            """,
            (tenant_id, run_id, maximum),
        )
        return RetryBudgetSnapshot(tenant_id, run_id, used=used + 1, maximum=maximum)

    def _mutate_owned(
        self,
        values: dict[str, Any],
        mutate: Callable[[ExecutionOwnership], ExecutionOwnership],
    ) -> ExecutionOwnership:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._validate_control_lease(values)
                current = self._read_required(values)
                _assert_owner_from_values(current, values)
                updated = mutate(current)
                self._write(updated, expected_revision=current.revision)
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def _read(self, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None:
        row = self._connection.execute(
            """
            SELECT ownership_json FROM workflow_execution_ownership
            WHERE tenant_id = ? AND run_id = ? AND step_id = ?
            """,
            (tenant_id, run_id, step_id),
        ).fetchone()
        return ExecutionOwnership.from_mapping(json.loads(str(row["ownership_json"]))) if row else None

    def _read_required(self, values: dict[str, Any]) -> ExecutionOwnership:
        current = self._read(str(values["tenant_id"]), str(values["run_id"]), str(values["step_id"]))
        if current is None:
            raise KeyError("execution_ownership_not_found")
        return current

    def _write(self, value: ExecutionOwnership, *, expected_revision: int) -> None:
        value.assert_valid()
        payload = canonical_json(value.to_dict())
        if expected_revision == 0:
            self._connection.execute(
                """
                INSERT INTO workflow_execution_ownership
                (tenant_id, run_id, step_id, revision, fencing_token, ownership_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (value.tenant_id, value.run_id, value.step_id, value.revision, value.fencing_token, payload),
            )
        else:
            cursor = self._connection.execute(
                """
                UPDATE workflow_execution_ownership
                SET revision = ?, fencing_token = ?, ownership_json = ?
                WHERE tenant_id = ? AND run_id = ? AND step_id = ? AND revision = ?
                """,
                (
                    value.revision,
                    value.fencing_token,
                    payload,
                    value.tenant_id,
                    value.run_id,
                    value.step_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise OptimisticConcurrencyError("execution_ownership_compare_and_set_failed")
        self._connection.execute(
            """
            INSERT INTO workflow_execution_attempt_history
            (tenant_id, run_id, step_id, revision, attempt_id, ownership_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (value.tenant_id, value.run_id, value.step_id, value.revision, value.attempt_id, payload),
        )
