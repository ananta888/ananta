"""SQLAlchemy implementation of the hub-owned side-effect ledger port."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import (
    WorkflowSideEffectLedgerDB,
    WorkflowTransitionSideEffectAuthorizationDB,
)
from agent.services.workflow_runtime.errors import (
    FencingTokenError,
    OptimisticConcurrencyError,
)
from agent.services.workflow_runtime.side_effects import (
    SideEffectClaim,
    SideEffectLedger,
    SideEffectRecord,
    WorkflowTransitionSideEffectAuthorizationIntent,
    WorkflowTransitionSideEffectAuthorizationObservation,
    WorkflowTransitionSideEffectAuthorizationReceipt,
    _assert_side_effect_row_projection,
    _binding,
    _new_record,
    _transition,
    _transition_authorization_commit_values,
    _transition_authorization_observation,
    assert_workflow_transition_side_effect_authorization_observation_digest,
)
from agent.services.workflow_runtime.sqlalchemy_support import SessionFactory, SQLAlchemyStoreSupport


class SQLAlchemySideEffectLedger(SQLAlchemyStoreSupport, SideEffectLedger):
    """CAS-protected ledger shared by Native, LangGraph, and Temporal runtimes.

    The adapter intentionally calls the existing domain transition functions;
    database code therefore cannot invent a second state machine.
    """

    def __init__(self, bind: Engine | SessionFactory) -> None:
        super().__init__(bind)

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
        candidate = _new_record(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            declared_operation=declared_operation,
            side_effect_class=side_effect_class,
        )
        try:
            with self._transaction() as session:
                current = session.get(WorkflowSideEffectLedgerDB, candidate.operation_id)
                if current is not None:
                    return _same_binding_or_raise(current, candidate)
                session.add(_ledger_row(candidate))
                session.flush()
                return SideEffectRecord.from_mapping(candidate.to_dict())
        except IntegrityError as exc:
            with self._read_session() as session:
                current = session.get(WorkflowSideEffectLedgerDB, candidate.operation_id)
                if current is not None:
                    return _same_binding_or_raise(current, candidate)
            raise OptimisticConcurrencyError("side_effect_plan_compare_and_set_failed") from exc

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
        with self._transaction() as session:
            row, current = self._required(session, operation_id, lock=True)
            if current.status == "completed":
                return SideEffectClaim(current, False, "already_completed")
            if (
                current.status == "started"
                and current.fencing_token == int(fencing_token)
                and current.attempt_id == str(attempt_id)
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
            self._compare_and_set(session, row, updated)
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
        with self._read_session() as session:
            row = session.get(WorkflowSideEffectLedgerDB, str(operation_id))
            if row is None or row.tenant_id != str(tenant_id):
                return None
            return _record(row)

    def observe_transition_authorization(
        self,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
    ) -> WorkflowTransitionSideEffectAuthorizationObservation:
        with self._read_session() as session:
            observation, _row = self._transition_authorization_observation(
                session,
                intent,
                lock=False,
            )
            return observation

    def authorize_transition_effect(
        self,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
        *,
        expected_observation_digest: str,
    ) -> WorkflowTransitionSideEffectAuthorizationReceipt:
        expected_digest = assert_workflow_transition_side_effect_authorization_observation_digest(
            expected_observation_digest
        )
        try:
            with self._transaction() as session:
                observation, ledger_row = self._transition_authorization_observation(
                    session,
                    intent,
                    lock=True,
                )
                if observation.receipt is not None:
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
                if ledger_row is None:
                    session.add(_ledger_row(authorized))
                else:
                    if planned.revision != int(ledger_row.revision):
                        raise OptimisticConcurrencyError(
                            "workflow_transition_side_effect_authorization_observation_conflict"
                        )
                    self._compare_and_set(session, ledger_row, authorized)
                session.add(_transition_authorization_row(receipt))
                session.flush()
                return receipt
        except (IntegrityError, OptimisticConcurrencyError) as exc:
            with self._read_session() as session:
                observation, _row = self._transition_authorization_observation(
                    session,
                    intent,
                    lock=False,
                )
                if observation.receipt is not None:
                    return observation.receipt
            if isinstance(exc, OptimisticConcurrencyError):
                raise
            raise OptimisticConcurrencyError(
                "workflow_transition_side_effect_authorization_compare_and_set_failed"
            ) from exc

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
        with self._transaction() as session:
            row, current = self._required(session, operation_id, lock=True)
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
            self._compare_and_set(session, row, updated)
            return updated

    def _transition_authorization_observation(
        self,
        session,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
        *,
        lock: bool,
    ) -> tuple[
        WorkflowTransitionSideEffectAuthorizationObservation,
        WorkflowSideEffectLedgerDB | None,
    ]:
        anchor = sa.select(sa.literal(intent.operation_id).label("operation_id")).subquery()
        receipt_predicate = sa.or_(
            WorkflowTransitionSideEffectAuthorizationDB.operation_id == intent.operation_id,
            WorkflowTransitionSideEffectAuthorizationDB.receipt_id == intent.receipt_id,
            WorkflowTransitionSideEffectAuthorizationDB.effect_id == intent.effect_id,
            WorkflowTransitionSideEffectAuthorizationDB.operation_fence_id == intent.operation_fence_id,
        )
        statement = (
            sa.select(
                WorkflowSideEffectLedgerDB,
                WorkflowTransitionSideEffectAuthorizationDB,
            )
            .select_from(
                anchor.outerjoin(
                    WorkflowSideEffectLedgerDB,
                    WorkflowSideEffectLedgerDB.operation_id == anchor.c.operation_id,
                ).outerjoin(
                    WorkflowTransitionSideEffectAuthorizationDB,
                    receipt_predicate,
                )
            )
            .order_by(
                WorkflowTransitionSideEffectAuthorizationDB.authorized_ledger_revision,
                WorkflowTransitionSideEffectAuthorizationDB.receipt_id,
            )
            .limit(1_001)
        )
        # One statement provides one READ COMMITTED snapshot.  Do not add
        # ``FOR UPDATE`` here: PostgreSQL rejects locks on nullable sides of
        # the two LEFT JOINs.  The write path is fenced by the ledger revision
        # CAS plus the append-only receipt uniqueness constraints.
        del lock
        rows = tuple(session.execute(statement).all())
        ledger_rows = tuple(row[0] for row in rows if row[0] is not None)
        if ledger_rows and any(row is not ledger_rows[0] for row in ledger_rows[1:]):
            raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_ledger_alias_conflict")
        ledger_row = ledger_rows[0] if ledger_rows else None
        receipt_rows = tuple(row[1] for row in rows if row[1] is not None)
        if len(receipt_rows) > 1_000:
            raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_history_limit")
        ledger_record = _record_exact(ledger_row) if ledger_row is not None else None
        receipts = tuple(_transition_authorization_receipt(row) for row in receipt_rows)
        return (
            _transition_authorization_observation(
                intent,
                ledger_record=ledger_record,
                receipts=receipts,
            ),
            ledger_row,
        )

    def _mutate(self, operation_id: str, **changes: object) -> SideEffectRecord:
        with self._transaction() as session:
            row, current = self._required(session, operation_id, lock=True)
            updated = _transition(current, **changes)
            self._compare_and_set(session, row, updated)
            return updated

    def _required(self, session, operation_id: str, *, lock: bool):
        statement = sa.select(WorkflowSideEffectLedgerDB).where(
            WorkflowSideEffectLedgerDB.operation_id == str(operation_id)
        )
        if lock:
            statement = self._for_update(statement)
        row = session.execute(statement).scalar_one_or_none()
        if row is None:
            raise KeyError("side_effect_operation_not_found")
        return row, _record(row)

    @staticmethod
    def _compare_and_set(session, row: WorkflowSideEffectLedgerDB, updated: SideEffectRecord) -> None:
        result = session.execute(
            sa.update(WorkflowSideEffectLedgerDB)
            .where(
                WorkflowSideEffectLedgerDB.operation_id == row.operation_id,
                WorkflowSideEffectLedgerDB.revision == row.revision,
            )
            .values(
                tenant_id=updated.tenant_id,
                workflow_id=updated.workflow_id,
                run_id=updated.run_id,
                step_id=updated.step_id,
                status=updated.status,
                revision=updated.revision,
                fencing_token=updated.fencing_token,
                updated_at=updated.updated_at,
                record=updated.to_dict(),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise OptimisticConcurrencyError("side_effect_compare_and_set_failed")


def _ledger_row(record: SideEffectRecord) -> WorkflowSideEffectLedgerDB:
    return WorkflowSideEffectLedgerDB(
        operation_id=record.operation_id,
        tenant_id=record.tenant_id,
        workflow_id=record.workflow_id,
        run_id=record.run_id,
        step_id=record.step_id,
        status=record.status,
        revision=record.revision,
        fencing_token=record.fencing_token,
        updated_at=record.updated_at,
        record=record.to_dict(),
    )


def _record(row: WorkflowSideEffectLedgerDB) -> SideEffectRecord:
    return SideEffectRecord.from_mapping(dict(row.record))


def _record_exact(row: WorkflowSideEffectLedgerDB) -> SideEffectRecord:
    try:
        record = SideEffectRecord.from_exact_mapping(dict(row.record))
    except (TypeError, ValueError) as exc:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_ledger_record_invalid") from exc
    _assert_side_effect_row_projection(
        record,
        operation_id=row.operation_id,
        tenant_id=row.tenant_id,
        workflow_id=row.workflow_id,
        run_id=row.run_id,
        step_id=row.step_id,
        status=row.status,
        revision=row.revision,
        fencing_token=row.fencing_token,
        updated_at=row.updated_at,
    )
    return record


def _transition_authorization_row(
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
) -> WorkflowTransitionSideEffectAuthorizationDB:
    return WorkflowTransitionSideEffectAuthorizationDB(
        receipt_id=receipt.receipt_id,
        transition_id=receipt.transition_id,
        effect_id=receipt.effect_id,
        operation_id=receipt.operation_id,
        operation_fence_id=receipt.operation_fence_id,
        tenant_id=receipt.tenant_id,
        workflow_id=receipt.workflow_id,
        run_id=receipt.run_id,
        runtime_id=receipt.runtime_id,
        step_id=receipt.step_id,
        operation_intent_digest=receipt.operation_intent_digest,
        authorization_envelope_id=receipt.authorization_envelope_id,
        authorization_envelope_digest=receipt.authorization_envelope_digest,
        ownership_attempt_id=receipt.ownership_attempt_id,
        ownership_fencing_token=receipt.ownership_fencing_token,
        creator_claim_generation=receipt.creator_claim_generation,
        authorized_ledger_revision=receipt.authorized_ledger_revision,
        planned_at=receipt.planned_at,
        authorized_at=receipt.authorized_at,
        receipt_digest=receipt.receipt_digest,
        receipt=receipt.to_dict(),
    )


def _transition_authorization_receipt(
    row: WorkflowTransitionSideEffectAuthorizationDB,
) -> WorkflowTransitionSideEffectAuthorizationReceipt:
    try:
        receipt = WorkflowTransitionSideEffectAuthorizationReceipt.from_mapping(dict(row.receipt))
    except (TypeError, ValueError) as exc:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_receipt_invalid") from exc
    projection = {
        "receipt_id": row.receipt_id,
        "transition_id": row.transition_id,
        "effect_id": row.effect_id,
        "operation_id": row.operation_id,
        "operation_fence_id": row.operation_fence_id,
        "tenant_id": row.tenant_id,
        "workflow_id": row.workflow_id,
        "run_id": row.run_id,
        "runtime_id": row.runtime_id,
        "step_id": row.step_id,
        "operation_intent_digest": row.operation_intent_digest,
        "authorization_envelope_id": row.authorization_envelope_id,
        "authorization_envelope_digest": row.authorization_envelope_digest,
        "ownership_attempt_id": row.ownership_attempt_id,
        "ownership_fencing_token": row.ownership_fencing_token,
        "creator_claim_generation": row.creator_claim_generation,
        "authorized_ledger_revision": row.authorized_ledger_revision,
        "planned_at": row.planned_at,
        "authorized_at": row.authorized_at,
        "receipt_digest": row.receipt_digest,
    }
    if any(getattr(receipt, name) != value for name, value in projection.items()):
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_receipt_projection_conflict")
    return receipt


def _same_binding_or_raise(row: WorkflowSideEffectLedgerDB, candidate: SideEffectRecord) -> SideEffectRecord:
    current = _record(row)
    if _binding(current) != _binding(candidate):
        raise OptimisticConcurrencyError("operation_id_binding_conflict")
    return current
