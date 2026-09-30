"""Per-Organization ordered append-only runtime event SQL store."""

from __future__ import annotations

import hashlib
from dataclasses import asdict

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from agent.db_models.organization_runtime import (
    OrganizationRuntimeEventDB,
)
from agent.db_models.organizations import OrganizationInstanceDB
from agent.models.organization_event import OrganizationEvent
from agent.repositories.organization_runtime_support import (
    SessionFactory,
    canonical_json,
    default_session,
)


class SqlOrganizationEventStore:
    """Per-Organization ordered append-only event store."""

    def __init__(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._project_id = project_id
        self._organization_id = organization_id
        self._session_factory = session_factory or default_session

    def append_once(self, event: OrganizationEvent) -> tuple[bool, OrganizationEvent]:
        if event.organization_id != self._organization_id:
            raise ValueError("organization_event_scope_mismatch")
        try:
            with self._session_factory() as session, session.begin():
                organization = session.exec(
                    select(OrganizationInstanceDB)
                    .where(OrganizationInstanceDB.tenant_id == self._tenant_id)
                    .where(OrganizationInstanceDB.project_id == self._project_id)
                    .where(OrganizationInstanceDB.organization_id == self._organization_id)
                    .with_for_update()
                ).first()
                if organization is None:
                    raise ValueError("organization_event_organization_not_found")
                existing = self._event(session, event.event_id)
                if existing is not None:
                    return False, self._domain_event(existing)
                last_sequence = session.exec(
                    select(sa.func.max(OrganizationRuntimeEventDB.sequence))
                    .where(OrganizationRuntimeEventDB.tenant_id == self._tenant_id)
                    .where(OrganizationRuntimeEventDB.project_id == self._project_id)
                    .where(OrganizationRuntimeEventDB.organization_id == self._organization_id)
                ).one()
                normalized = OrganizationEvent(
                    **{
                        **asdict(event),
                        "sequence": int(last_sequence or 0) + 1,
                    }
                )
                session.add(
                    OrganizationRuntimeEventDB(
                        tenant_id=self._tenant_id,
                        project_id=self._project_id,
                        organization_id=self._organization_id,
                        event_id=normalized.event_id,
                        event_type=normalized.event_type,
                        definition_revision=normalized.definition_revision,
                        snapshot_hash=normalized.snapshot_hash,
                        correlation_id=normalized.correlation_id,
                        sequence=normalized.sequence,
                        occurred_at=normalized.occurred_at,
                        payload_json=dict(normalized.payload),
                        semantic_digest=self._semantic_digest(normalized),
                    )
                )
            return True, normalized
        except (IntegrityError, OperationalError):
            with self._session_factory() as session:
                existing = self._event(session, event.event_id)
                if existing is None:
                    raise ValueError("organization_event_append_race") from None
                return False, self._domain_event(existing)

    def list_for_organization(self, organization_id: str) -> tuple[OrganizationEvent, ...]:
        if organization_id != self._organization_id:
            return ()
        with self._session_factory() as session:
            rows = session.exec(
                select(OrganizationRuntimeEventDB)
                .where(OrganizationRuntimeEventDB.tenant_id == self._tenant_id)
                .where(OrganizationRuntimeEventDB.project_id == self._project_id)
                .where(OrganizationRuntimeEventDB.organization_id == self._organization_id)
                .order_by(OrganizationRuntimeEventDB.sequence)
            ).all()
            return tuple(self._domain_event(row) for row in rows)

    def _event(
        self,
        session: Session,
        event_id: str,
    ) -> OrganizationRuntimeEventDB | None:
        return session.exec(
            select(OrganizationRuntimeEventDB)
            .where(OrganizationRuntimeEventDB.tenant_id == self._tenant_id)
            .where(OrganizationRuntimeEventDB.project_id == self._project_id)
            .where(OrganizationRuntimeEventDB.organization_id == self._organization_id)
            .where(OrganizationRuntimeEventDB.event_id == event_id)
        ).first()

    @staticmethod
    def _domain_event(row: OrganizationRuntimeEventDB) -> OrganizationEvent:
        event = OrganizationEvent(
            event_id=row.event_id,
            event_type=row.event_type,
            organization_id=row.organization_id,
            definition_revision=row.definition_revision,
            snapshot_hash=row.snapshot_hash,
            correlation_id=row.correlation_id,
            sequence=row.sequence,
            occurred_at=row.occurred_at,
            payload=dict(row.payload_json or {}),
        )
        if row.semantic_digest != SqlOrganizationEventStore._semantic_digest(event):
            raise ValueError("organization_event_integrity_mismatch")
        return event

    @staticmethod
    def _semantic_digest(event: OrganizationEvent) -> str:
        payload = asdict(event)
        payload.pop("sequence", None)
        payload.pop("occurred_at", None)
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


__all__ = [
    "SqlOrganizationEventStore",
]
