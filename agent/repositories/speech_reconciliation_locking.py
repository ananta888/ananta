"""Row locking and fence checks shared by speech reconciliation mutations."""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager, nullcontext

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_reconciliation import (
    SpeechReconciliationAttemptDB,
    SpeechReconciliationJobDB,
)
from agent.repositories.speech_reconciliation_records import SpeechReconciliationRepositoryError

_SQLITE_WRITE_GUARD = threading.RLock()


def sqlite_write_guard() -> AbstractContextManager:
    """Serialize writers that share one StaticPool SQLite connection.

    Tests and native single-process deployments use one StaticPool SQLite
    connection. The guard prevents two threads from rolling back each other's
    connection-level transaction; database uniqueness constraints remain the
    cross-process/multi-Hub authority.
    """

    return _SQLITE_WRITE_GUARD if engine.dialect.name == "sqlite" else nullcontext()


def locked_job(session: Session, tenant_id: str, owner_subject: str, job_id: str) -> SpeechReconciliationJobDB:
    row = session.exec(
        select(SpeechReconciliationJobDB)
        .where(
            SpeechReconciliationJobDB.id == job_id,
            SpeechReconciliationJobDB.tenant_id == tenant_id,
            SpeechReconciliationJobDB.owner_subject == owner_subject,
        )
        .with_for_update()
    ).first()
    if row is None:
        raise SpeechReconciliationRepositoryError("speech_reconciliation_job_not_found", status_code=404)
    return row


def locked_attempt(session: Session, job_id: str, attempt_id: str) -> SpeechReconciliationAttemptDB:
    row = session.exec(
        select(SpeechReconciliationAttemptDB)
        .where(SpeechReconciliationAttemptDB.id == attempt_id, SpeechReconciliationAttemptDB.job_id == job_id)
        .with_for_update()
    ).first()
    if row is None:
        raise SpeechReconciliationRepositoryError("speech_reconciliation_attempt_not_found", status_code=404)
    return row


def require_current_fence(job, attempt, epoch: int, token_digest: str, now_ms: int) -> None:
    if (
        job.active_attempt_id != attempt.id
        or job.fencing_epoch != epoch
        or attempt.fencing_epoch != epoch
        or attempt.fencing_token_digest != token_digest
        or attempt.state != "running"
        or attempt.lease_expires_at_ms <= now_ms
    ):
        raise SpeechReconciliationRepositoryError("speech_reconciliation_fence_stale")


__all__ = ["locked_attempt", "locked_job", "require_current_fence", "sqlite_write_guard"]
