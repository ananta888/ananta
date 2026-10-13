"""Bounded reserved-run lookup and atomic terminal cleanup; no identity issuance."""

from sqlmodel import Session, select

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from agent.db_models.tasks import TaskDB
from agent.repositories.evidence_identity_rows import apply_run_result, run_identity
from agent.repositories.sqlalchemy_support import sqlite_transaction_guard


class SqlCompanionRunCleanup:
    def __init__(self, database):
        self.database = database

    def reserved(self, *, tenant_id, project_id, task_id, assignment_id, dispatch_lease_id):
        with sqlite_transaction_guard(self.database), Session(self.database) as session:
            rows = session.exec(select(HubRunEvidenceIdentityDB).where(
                HubRunEvidenceIdentityDB.tenant_id == tenant_id,
                HubRunEvidenceIdentityDB.project_id == project_id,
                HubRunEvidenceIdentityDB.task_id == task_id,
                HubRunEvidenceIdentityDB.assignment_id == assignment_id,
                HubRunEvidenceIdentityDB.dispatch_lease_id == dispatch_lease_id,
                HubRunEvidenceIdentityDB.state == "reserved",
            ).order_by(HubRunEvidenceIdentityDB.run_id).limit(480)).all()
            return tuple(run_identity(row) for row in rows)

    def terminate(self, identity, *, terminal_state, result_digest, updated_at_epoch):
        with sqlite_transaction_guard(self.database), Session(self.database) as session:
            row = session.exec(select(HubRunEvidenceIdentityDB).where(
                HubRunEvidenceIdentityDB.tenant_id == identity.tenant_id,
                HubRunEvidenceIdentityDB.project_id == identity.project_id,
                HubRunEvidenceIdentityDB.run_id == identity.run_id,
            ).with_for_update()).first()
            # A result that won the race is immutable. Changed reservations must
            # be revalidated by the Registry on a subsequent Hub cleanup attempt.
            if row is None or run_identity(row) != identity:
                return False
            apply_run_result(row, assignment_id=identity.assignment_id,
                dispatch_lease_id=identity.dispatch_lease_id, terminal_state=terminal_state,
                result_digest=result_digest, updated_at_epoch=updated_at_epoch)
            session.add(row)
            session.commit()
            return True

    def unchanged(self, identity):
        with sqlite_transaction_guard(self.database), Session(self.database) as session:
            row = session.get(HubRunEvidenceIdentityDB,
                              (identity.tenant_id, identity.project_id, identity.run_id))
            return row is not None and run_identity(row) == identity

    def terminal_tasks(self, after, limit):
        if type(limit) is not int or not 1 <= limit <= 100 or after is not None and not isinstance(after, str):
            raise ValueError("meet_companion_cleanup_page_invalid")
        reserved = select(HubRunEvidenceIdentityDB.run_id).where(
            HubRunEvidenceIdentityDB.task_id == TaskDB.id,
            HubRunEvidenceIdentityDB.tenant_id == TaskDB.tenant_id,
            HubRunEvidenceIdentityDB.project_id == TaskDB.project_id,
            HubRunEvidenceIdentityDB.state == "reserved",
        ).exists()
        query = select(TaskDB).where(TaskDB.task_kind == "meet_dialog_session",
            TaskDB.status.in_({"completed", "failed", "cancelled"}), reserved)
        if after is not None:
            query = query.where(TaskDB.id > after)
        with sqlite_transaction_guard(self.database), Session(self.database) as session:
            return list(session.exec(query.order_by(TaskDB.id).limit(limit)).all())
