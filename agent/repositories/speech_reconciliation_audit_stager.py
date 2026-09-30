"""Transactional audit staging shared by the speech reconciliation collaborators."""

from __future__ import annotations

from sqlmodel import Session

from agent.models.semantic_media_audit import SemanticMediaAuditEvent
from agent.ports.semantic_media_audit import SemanticMediaAuditPort
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox


class SpeechReconciliationAuditStager:
    """Prepares content-free audit events and stages them in the caller's session.

    One instance is shared by every collaborator of the repository facade so a
    late ``configure`` from the composition root reaches all mutation paths.
    """

    def __init__(self, audit: SemanticMediaAuditPort | None = None) -> None:
        self._audit = audit

    @property
    def enabled(self) -> bool:
        return self._audit is not None

    def configure(self, audit: SemanticMediaAuditPort | None) -> None:
        self._audit = audit

    def prepare(
        self,
        *,
        tenant_id: str,
        job_id: str,
        idempotency_key: str,
        event_type: str,
        transition: str,
        reason_code: str,
        epoch: int,
        lease_ref: str | None = None,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        return self._audit.prepare_transition(
            idempotency_key=idempotency_key,
            tenant_id=tenant_id,
            scope=f"speech-job:{job_id}",
            event_type=event_type,
            transition=transition,
            reason_code=reason_code,
            epoch=max(1, epoch),
            lease_ref=lease_ref,
            job_ref=job_id,
        )

    @staticmethod
    def enqueue(session: Session, event: SemanticMediaAuditEvent | None) -> None:
        if event is not None:
            SqlSemanticMediaAuditOutbox.enqueue_in_session(session, event)


__all__ = ["SpeechReconciliationAuditStager"]
