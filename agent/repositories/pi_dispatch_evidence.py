"""Caller-session persistence of closed Pi assignments, without identity issuance."""

from sqlalchemy import select

from agent.db_models.native_dispatch import NativeDispatchDB
from agent.db_models.pi_dispatch_evidence import PiDispatchEvidenceDB
from agent.repositories.sqlalchemy_support import require_locked_row, stable_row_id
from agent.services.native_dispatch_binding import native_dispatch_binding_digest
from ananta_contracts.hub_evidence import validate_hub_evidence_assignment


class TransactionPiDispatchEvidenceRepository:
    def __init__(self, session):
        if not session.in_transaction():
            raise ValueError('pi_dispatch_evidence_transaction_required')
        self._session = session

    def add(self, *, task, claim, projection):
        projection = validate_hub_evidence_assignment(projection)
        if projection['task_id'] != task.id:
            raise ValueError('pi_dispatch_evidence_task_mismatch')
        self._session.add(PiDispatchEvidenceDB(tenant_id=task.tenant_id, task_id=task.id,
            project_id=task.project_id, claim=claim, projection=projection))
        self._session.flush()

    def require(self, *, task):
        journal = require_locked_row(self._session, NativeDispatchDB,
            stable_row_id('native-dispatch', task.tenant_id, task.id), missing_reason='pi_dispatch_journal_missing')
        statement = select(PiDispatchEvidenceDB).where(
            PiDispatchEvidenceDB.tenant_id == task.tenant_id, PiDispatchEvidenceDB.task_id == task.id)
        if self._session.get_bind().dialect.name == 'postgresql':
            statement = statement.with_for_update()
        row = self._session.execute(statement.execution_options(populate_existing=True)).scalar_one_or_none()
        if (row is None or row.project_id != task.project_id or row.claim != journal.claim
                or journal.binding_digest != native_dispatch_binding_digest(task, worker_id=journal.worker_id,
                    worker_url=journal.worker_url, assignment_revision=journal.assignment_revision)):
            raise ValueError('pi_dispatch_evidence_journal_binding_denied')
        projection = validate_hub_evidence_assignment(row.projection)
        if projection['task_id'] != task.id:
            raise ValueError('pi_dispatch_evidence_task_mismatch')
        return projection
