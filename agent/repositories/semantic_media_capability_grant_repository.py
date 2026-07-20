"""Persistence ports for Hub-owned semantic-media capability grants."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import SemanticMediaCapabilityGrantDB
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent
from ananta_contracts.semantic_media_permissions import SemanticMediaCapabilityGrant


class SemanticMediaCapabilityGrantRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class PersistedSemanticMediaCapabilityGrant:
    grant: SemanticMediaCapabilityGrant
    revoked_at: float | None = None
    revoked_by: str | None = None
    revocation_version: int = 0


def same_idempotent_grant_request(
    first: SemanticMediaCapabilityGrant,
    second: SemanticMediaCapabilityGrant,
) -> bool:
    """Compare immutable issuance input while preserving first-write time/signature."""

    fields = (
        "version",
        "grant_id",
        "owner_id",
        "tenant_id",
        "subject_id",
        "subject_role",
        "capability",
        "scope_kind",
        "scope_id",
        "direction",
        "data_type",
        "purpose",
        "epoch",
        "expires_at",
        "issuer",
    )
    return all(getattr(first, field) == getattr(second, field) for field in fields)


class SemanticMediaCapabilityGrantRepository(Protocol):
    """Narrow persistence interface consumed by the permission domain service."""

    def create(
        self,
        grant: SemanticMediaCapabilityGrant,
        *,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> PersistedSemanticMediaCapabilityGrant: ...

    def get(self, grant_id: str) -> PersistedSemanticMediaCapabilityGrant | None: ...

    def revoke(
        self,
        grant_id: str,
        *,
        tenant_id: str,
        revoked_by: str,
        revoked_at: float,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> PersistedSemanticMediaCapabilityGrant | None: ...

    def list_scope(
        self,
        *,
        tenant_id: str,
        scope_kind: str,
        scope_id: str,
        epoch: int,
        owner_id: str | None,
        subject_id: str | None,
        limit: int,
    ) -> tuple[PersistedSemanticMediaCapabilityGrant, ...]: ...

class InMemorySemanticMediaCapabilityGrantRepository:
    """Deterministic test adapter; production composition always uses SQL."""

    def __init__(self) -> None:
        self._records: dict[str, PersistedSemanticMediaCapabilityGrant] = {}
        self._lock = threading.RLock()

    def create(
        self,
        grant: SemanticMediaCapabilityGrant,
        *,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> PersistedSemanticMediaCapabilityGrant:
        del audit_event
        candidate = PersistedSemanticMediaCapabilityGrant(grant)
        with self._lock:
            existing = self._records.get(grant.grant_id)
            if existing is not None:
                if same_idempotent_grant_request(existing.grant, grant):
                    return existing
                raise SemanticMediaCapabilityGrantRepositoryError("capability_grant_id_conflict")
            self._records[grant.grant_id] = candidate
        return candidate

    def get(self, grant_id: str) -> PersistedSemanticMediaCapabilityGrant | None:
        with self._lock:
            return self._records.get(grant_id)

    def revoke(
        self,
        grant_id: str,
        *,
        tenant_id: str,
        revoked_by: str,
        revoked_at: float,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> PersistedSemanticMediaCapabilityGrant | None:
        del audit_event
        with self._lock:
            current = self._records.get(grant_id)
            if current is None or current.grant.tenant_id != tenant_id:
                return None
            if current.revoked_at is not None:
                return current
            updated = PersistedSemanticMediaCapabilityGrant(
                current.grant,
                revoked_at=revoked_at,
                revoked_by=revoked_by,
                revocation_version=current.revocation_version + 1,
            )
            self._records[grant_id] = updated
            return updated

    def list_scope(
        self,
        *,
        tenant_id: str,
        scope_kind: str,
        scope_id: str,
        epoch: int,
        owner_id: str | None,
        subject_id: str | None,
        limit: int,
    ) -> tuple[PersistedSemanticMediaCapabilityGrant, ...]:
        with self._lock:
            matches = [
                item
                for item in self._records.values()
                if item.grant.tenant_id == tenant_id
                and item.grant.scope_kind == scope_kind
                and item.grant.scope_id == scope_id
                and item.grant.epoch == epoch
                and (owner_id is None or item.grant.owner_id == owner_id)
                and (subject_id is None or item.grant.subject_id == subject_id)
            ]
        matches.sort(key=lambda item: (item.grant.issued_at, item.grant.grant_id), reverse=True)
        return tuple(matches[:limit])

class SqlSemanticMediaCapabilityGrantRepository:
    """Cross-process SQL source of truth with an atomic revocation CAS."""

    def __init__(self, *, db_engine=default_engine) -> None:
        self._engine = db_engine

    def create(
        self,
        grant: SemanticMediaCapabilityGrant,
        *,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> PersistedSemanticMediaCapabilityGrant:
        if audit_event is None:
            raise SemanticMediaCapabilityGrantRepositoryError("capability_audit_required")
        row = self._to_row(grant)
        with Session(self._engine) as db:
            existing = db.get(SemanticMediaCapabilityGrantDB, grant.grant_id)
            if existing is not None:
                persisted = self._from_row(existing)
                if not same_idempotent_grant_request(persisted.grant, grant):
                    raise SemanticMediaCapabilityGrantRepositoryError(
                        "capability_grant_id_conflict"
                    )
                SqlSemanticMediaAuditOutbox.enqueue_in_session(db, audit_event)
                db.commit()
                return persisted
            db.add(row)
            SqlSemanticMediaAuditOutbox.enqueue_in_session(db, audit_event)
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                existing = db.get(SemanticMediaCapabilityGrantDB, grant.grant_id)
                if existing is None or not same_idempotent_grant_request(
                    self._from_row(existing).grant,
                    grant,
                ):
                    raise SemanticMediaCapabilityGrantRepositoryError(
                        "capability_grant_id_conflict"
                    ) from exc
                SqlSemanticMediaAuditOutbox.enqueue_in_session(db, audit_event)
                db.commit()
                return self._from_row(existing)
            db.refresh(row)
            return self._from_row(row)

    def get(self, grant_id: str) -> PersistedSemanticMediaCapabilityGrant | None:
        with Session(self._engine) as db:
            row = db.get(SemanticMediaCapabilityGrantDB, grant_id)
            return self._from_row(row) if row is not None else None

    def revoke(
        self,
        grant_id: str,
        *,
        tenant_id: str,
        revoked_by: str,
        revoked_at: float,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> PersistedSemanticMediaCapabilityGrant | None:
        if audit_event is None:
            raise SemanticMediaCapabilityGrantRepositoryError("capability_audit_required")
        with Session(self._engine) as db:
            row = db.exec(
                select(SemanticMediaCapabilityGrantDB)
                .where(
                    SemanticMediaCapabilityGrantDB.id == grant_id,
                    SemanticMediaCapabilityGrantDB.tenant_id == tenant_id,
                )
                .with_for_update()
            ).first()
            if row is None:
                return None
            if row.revoked_at is None:
                row.revoked_at = revoked_at
                row.revoked_by = revoked_by
                row.revocation_version += 1
                row.updated_at = revoked_at
                db.add(row)
            SqlSemanticMediaAuditOutbox.enqueue_in_session(db, audit_event)
            db.commit()
            # A concurrent winner is an idempotent success.  The persisted
            # actor/time remain authoritative and are never overwritten.
            db.refresh(row)
            return self._from_row(row)

    def list_scope(
        self,
        *,
        tenant_id: str,
        scope_kind: str,
        scope_id: str,
        epoch: int,
        owner_id: str | None,
        subject_id: str | None,
        limit: int,
    ) -> tuple[PersistedSemanticMediaCapabilityGrant, ...]:
        statement = select(SemanticMediaCapabilityGrantDB).where(
            SemanticMediaCapabilityGrantDB.tenant_id == tenant_id,
            SemanticMediaCapabilityGrantDB.scope_kind == scope_kind,
            SemanticMediaCapabilityGrantDB.scope_id == scope_id,
            SemanticMediaCapabilityGrantDB.epoch == epoch,
        )
        if subject_id is not None:
            statement = statement.where(SemanticMediaCapabilityGrantDB.subject_id == subject_id)
        if owner_id is not None:
            statement = statement.where(SemanticMediaCapabilityGrantDB.owner_id == owner_id)
        with Session(self._engine) as db:
            rows = list(
                db.exec(
                    statement.order_by(
                        SemanticMediaCapabilityGrantDB.issued_at.desc(),
                        SemanticMediaCapabilityGrantDB.id.desc(),
                    ).limit(limit)
                )
            )
        return tuple(self._from_row(row) for row in rows)

    @staticmethod
    def _to_row(grant: SemanticMediaCapabilityGrant) -> SemanticMediaCapabilityGrantDB:
        return SemanticMediaCapabilityGrantDB(
            id=grant.grant_id,
            version=grant.version,
            owner_id=grant.owner_id,
            tenant_id=grant.tenant_id,
            subject_id=grant.subject_id,
            subject_role=grant.subject_role,
            capability=grant.capability,
            scope_kind=grant.scope_kind,
            scope_id=grant.scope_id,
            direction=grant.direction,
            data_type=grant.data_type,
            purpose=grant.purpose,
            epoch=grant.epoch,
            issued_at=grant.issued_at,
            expires_at=grant.expires_at,
            issuer=grant.issuer,
            signature=grant.signature,
            created_at=grant.issued_at,
            updated_at=grant.issued_at,
        )

    @staticmethod
    def _from_row(row: SemanticMediaCapabilityGrantDB) -> PersistedSemanticMediaCapabilityGrant:
        return PersistedSemanticMediaCapabilityGrant(
            SemanticMediaCapabilityGrant(
                version=row.version,
                grant_id=row.id,
                owner_id=row.owner_id,
                tenant_id=row.tenant_id,
                subject_id=row.subject_id,
                subject_role=row.subject_role,  # type: ignore[arg-type]
                capability=row.capability,  # type: ignore[arg-type]
                scope_kind=row.scope_kind,  # type: ignore[arg-type]
                scope_id=row.scope_id,
                direction=row.direction,  # type: ignore[arg-type]
                data_type=row.data_type,
                purpose=row.purpose,
                epoch=row.epoch,
                issued_at=row.issued_at,
                expires_at=row.expires_at,
                issuer=row.issuer,  # type: ignore[arg-type]
                signature=row.signature,
            ),
            revoked_at=row.revoked_at,
            revoked_by=row.revoked_by,
            revocation_version=row.revocation_version,
        )


__all__ = [
    "InMemorySemanticMediaCapabilityGrantRepository",
    "PersistedSemanticMediaCapabilityGrant",
    "SemanticMediaCapabilityGrantRepository",
    "SemanticMediaCapabilityGrantRepositoryError",
    "SqlSemanticMediaCapabilityGrantRepository",
    "same_idempotent_grant_request",
]
