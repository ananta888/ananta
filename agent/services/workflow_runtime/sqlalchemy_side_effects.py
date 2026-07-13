"""SQLAlchemy implementation of the hub-owned side-effect ledger port."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import WorkflowSideEffectLedgerDB
from agent.services.workflow_runtime.errors import (
    FencingTokenError,
    OptimisticConcurrencyError,
)
from agent.services.workflow_runtime.side_effects import (
    SideEffectClaim,
    SideEffectLedger,
    SideEffectRecord,
    _binding,
    _new_record,
    _transition,
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


def _same_binding_or_raise(
    row: WorkflowSideEffectLedgerDB, candidate: SideEffectRecord
) -> SideEffectRecord:
    current = _record(row)
    if _binding(current) != _binding(candidate):
        raise OptimisticConcurrencyError("operation_id_binding_conflict")
    return current
