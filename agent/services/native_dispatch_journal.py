"""Durable at-most-one send boundary; unknown outcomes require Hub reconciliation."""

import time
import uuid
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from agent.db_models.native_dispatch import NativeDispatchDB
from agent.db_models.tasks import TaskDB
from agent.repositories.sqlalchemy_support import SQLAlchemyStoreSupport, require_locked_row, stable_row_id
from agent.services.native_dispatch_binding import native_dispatch_binding_digest
from agent.services.native_result_task_projection import native_task_command, require_native_result_receipt
from agent.services.pi_result_receipt import require_pi_result_receipt
from agent.services.pi_result_task_projection import pi_task_result_candidate
from ananta_contracts.native_context_bundle import native_context_digest


class NativeDispatchJournalPort(Protocol):
    def begin(self, *, task, assignment) -> str: ...
    def settle(self, *, task, claim: str, accepted: bool) -> None: ...
    def reconcile(self, *, task) -> bool: ...


def _dispatch_receipt(task):
    command = native_task_command(task)
    if command.node.task_kind == "pi_coding_agent":
        return require_pi_result_receipt(task=task, command=command,
                                         candidate=pi_task_result_candidate(task, command))
    require_native_result_receipt(task, command)
    return task.verification_status["native_result_receipt"]


def require_native_dispatch_receipt(task):
    """Share terminal admission proof with Hub lifecycle policies."""
    return _dispatch_receipt(task)


class SQLAlchemyNativeDispatchJournal(SQLAlchemyStoreSupport):
    """A unique tenant/Task slot survives process loss and denies blind resends."""

    def __init__(self, bind, *, clock=time.time):
        super().__init__(bind)
        self._clock = clock

    @staticmethod
    def _identity(task):
        return stable_row_id("native-dispatch", task.tenant_id, task.id)

    def begin(self, *, task, assignment) -> str:
        try:
            with self._transaction() as session:
                return self.begin_in_transaction(session=session, task=task, assignment=assignment)
        except IntegrityError as exc:
            raise ValueError("native_dispatch_already_reserved") from exc

    def begin_in_transaction(self, *, session, task, assignment) -> str:
        """Flush the send fence in the caller's Task/Registry transaction.

        The caller owns commit, rollback and SQLite serialization. No transport
        may use the returned claim until that transaction has committed.
        Integrity conflicts deliberately poison the caller transaction: callers
        must roll back the entire reservation rather than retain an orphan RUN.
        """
        if not session.in_transaction():
            raise ValueError("native_dispatch_caller_transaction_required")
        stored = require_locked_row(session, TaskDB, task.id, missing_reason="native_dispatch_task_missing")
        if (stored.tenant_id != task.tenant_id or native_dispatch_binding_digest(
                stored, worker_id=assignment.worker_id, worker_url=assignment.worker_url,
                assignment_revision=assignment.revision) != native_dispatch_binding_digest(
                task, worker_id=assignment.worker_id, worker_url=assignment.worker_url,
                assignment_revision=assignment.revision)):
            raise ValueError("native_dispatch_current_task_binding_mismatch")
        command = native_task_command(task)
        if (not assignment.worker_id or assignment.worker_url != task.assigned_agent_url
                or type(assignment.revision) is not int or assignment.revision < 1):
            raise ValueError("native_dispatch_assignment_invalid")
        claim = uuid.uuid4().hex  # Internal CAS token, never an evidence identity.
        now = self._clock()
        row = NativeDispatchDB(
            id=self._identity(task), task_id=task.id, tenant_id=task.tenant_id,
            binding_digest=native_dispatch_binding_digest(
                task, worker_id=assignment.worker_id, worker_url=assignment.worker_url,
                assignment_revision=assignment.revision), command_digest=native_context_digest(command.to_dict()),
            worker_id=assignment.worker_id, worker_url=assignment.worker_url,
            assignment_revision=assignment.revision, claim=claim, state="sending", created_at=now, updated_at=now,
        )
        session.add(row)
        session.flush()
        return claim

    def settle(self, *, task, claim: str, accepted: bool) -> None:
        with self._transaction() as session:
            stored = None
            if accepted:
                # Match Task-before-journal lock ordering in result admission.
                stored = session.execute(self._for_update(select(TaskDB).where(
                    TaskDB.id == task.id, TaskDB.tenant_id == task.tenant_id,
                ))).scalar_one_or_none()
                if stored is None:
                    raise ValueError("native_dispatch_task_missing")
                _dispatch_receipt(stored)
            row = session.execute(self._for_update(select(NativeDispatchDB).where(
                NativeDispatchDB.id == self._identity(task)))).scalar_one_or_none()
            if row is None or row.claim != claim:
                raise ValueError("native_dispatch_claim_mismatch")
            if accepted and not self._matches_receipt(row, stored):
                raise ValueError("native_dispatch_receipt_binding_mismatch")
            # An unknown transport outcome can never undo a reconciled receipt.
            if row.state == "accepted":
                return
            changed = session.execute(update(NativeDispatchDB).where(
                NativeDispatchDB.id == row.id, NativeDispatchDB.claim == claim,
                NativeDispatchDB.state == row.state,
            ).values(state="accepted" if accepted else "unknown", updated_at=self._clock()))
            if changed.rowcount != 1:
                raise ValueError("native_dispatch_settlement_conflict")

    @staticmethod
    def _matches_receipt(row, task):
        receipt = _dispatch_receipt(task)
        authority = receipt.get("authority", {})
        return (receipt.get("command_digest") == row.command_digest
                and authority.get("assignment_revision") == row.assignment_revision
                and authority.get("worker_id") == row.worker_id
                and authority.get("worker_url") == row.worker_url
                and task.assigned_agent_url == row.worker_url)

    def reconcile(self, *, task) -> bool:
        """Close a pending send only from an independently admitted Task receipt."""
        with self._read_session() as session:
            row = session.get(NativeDispatchDB, self._identity(task))
            if row is None:
                raise ValueError("native_dispatch_receipt_binding_mismatch")
            claim = row.claim
        self.settle(task=task, claim=claim, accepted=True)
        return True

    def require_recovery_assignment(self, *, task, assignment):
        with self._read_session() as session:
            row = session.get(NativeDispatchDB, self._identity(task))
            if (row is None or row.state not in {"sending", "unknown"}
                    or row.binding_digest != native_dispatch_binding_digest(
                task, worker_id=assignment.worker_id, worker_url=assignment.worker_url,
                assignment_revision=assignment.revision)):
                raise ValueError("native_dispatch_recovery_binding_denied")

    def pending_task_ids(self, *, tenant_id: str, limit: int = 100, task_id: str | None = None) -> tuple[str, ...]:
        if not tenant_id or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("native_dispatch_scan_invalid")
        with self._read_session() as session:
            statement = select(NativeDispatchDB.task_id).where(
                NativeDispatchDB.tenant_id == tenant_id,
                NativeDispatchDB.state.in_(("sending", "unknown")),
            ).order_by(NativeDispatchDB.created_at, NativeDispatchDB.id).limit(limit)
            if task_id is not None:
                statement = statement.where(NativeDispatchDB.task_id == task_id)
            return tuple(session.execute(statement).scalars())


def production_native_dispatch_journal():
    from agent.database import engine

    return SQLAlchemyNativeDispatchJournal(engine)
