from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select

from agent.database import engine
from agent.db_models import VoiceRuntimeCleanupDB
from agent.services.voice_governance_domain import VoicePrincipal


@dataclass(frozen=True)
class VoiceRuntimeCleanupRecordInput:
    source_session_id: str
    cleanup_kind: str
    runtime_session_ciphertext: str | None = None
    target_digest: str | None = None
    initial_state: str = "pending"


@dataclass(frozen=True)
class VoiceRuntimeCleanupStatus:
    pending_count: int
    failed_count: int


class VoiceRuntimeCleanupRepository:
    """Tenant-scoped persistence port for the runtime-cleanup outbox."""

    def stage_many(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        operation: str,
        targets: Iterable[VoiceRuntimeCleanupRecordInput],
    ) -> int:
        values = tuple(targets)
        if not values:
            return 0
        if any(target.initial_state not in {"pending", "provisional"} for target in values):
            raise ValueError("voice runtime cleanup initial state is invalid")
        for attempt in range(2):
            with Session(engine) as session:
                created = 0
                for target in values:
                    existing = self._find_source(
                        session,
                        principal,
                        profile_id=profile_id,
                        source_session_id=target.source_session_id,
                    )
                    if existing is not None:
                        if (
                            existing.cleanup_kind != target.cleanup_kind
                            or existing.target_digest != target.target_digest
                        ):
                            raise RuntimeError("voice runtime cleanup target scope conflict")
                        if target.initial_state == "pending" and existing.state == "provisional":
                            existing.operation = operation
                            existing.state = "pending"
                            existing.updated_at = time.time()
                            session.add(existing)
                        continue
                    session.add(
                        VoiceRuntimeCleanupDB(
                            tenant_id=principal.tenant_id,
                            owner_subject=principal.subject,
                            profile_id=profile_id,
                            source_session_id=target.source_session_id,
                            operation=operation,
                            cleanup_kind=target.cleanup_kind,
                            runtime_session_ciphertext=target.runtime_session_ciphertext,
                            target_digest=target.target_digest,
                            state=target.initial_state,
                        )
                    )
                    created += 1
                try:
                    session.commit()
                    return created
                except IntegrityError:
                    session.rollback()
                    if attempt:
                        raise
        raise RuntimeError("voice runtime cleanup staging failed")

    def list_scope(
        self,
        principal: VoicePrincipal,
        profile_id: str,
    ) -> tuple[VoiceRuntimeCleanupDB, ...]:
        with Session(engine) as session:
            rows = session.exec(
                select(VoiceRuntimeCleanupDB)
                .where(*self._scope_predicates(principal, profile_id))
                .order_by(VoiceRuntimeCleanupDB.created_at, VoiceRuntimeCleanupDB.id)
            ).all()
            return tuple(rows)

    def pseudonymize_scope(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        *,
        replacement_principal: VoicePrincipal,
        replacement_profile_id: str,
    ) -> int:
        """Remove direct identifiers while preserving durable retry work."""

        with Session(engine) as session:
            rows = list(
                session.exec(
                    select(VoiceRuntimeCleanupDB).where(
                        *self._scope_predicates(principal, profile_id)
                    )
                ).all()
            )
            changed = 0
            for row in rows:
                existing = self._find_source(
                    session,
                    replacement_principal,
                    profile_id=replacement_profile_id,
                    source_session_id=row.source_session_id,
                )
                if existing is not None:
                    if (
                        existing.cleanup_kind != row.cleanup_kind
                        or existing.target_digest != row.target_digest
                        or existing.operation != row.operation
                    ):
                        raise RuntimeError("pseudonymous runtime cleanup target conflict")
                    session.delete(row)
                    changed += 1
                    continue
                row.tenant_id = replacement_principal.tenant_id
                row.owner_subject = replacement_principal.subject
                row.profile_id = replacement_profile_id
                row.updated_at = time.time()
                session.add(row)
                changed += 1
            session.commit()
            return changed

    def list_pending_scopes(
        self,
        *,
        limit: int = 100,
        include_provisional: bool = False,
    ) -> tuple[tuple[VoicePrincipal, str], ...]:
        bounded_limit = max(1, min(int(limit), 1_000))
        with Session(engine) as session:
            statement = select(
                    VoiceRuntimeCleanupDB.tenant_id,
                    VoiceRuntimeCleanupDB.owner_subject,
                    VoiceRuntimeCleanupDB.profile_id,
                )
            if not include_provisional:
                statement = statement.where(VoiceRuntimeCleanupDB.state != "provisional")
            rows = session.exec(
                statement.distinct()
                .order_by(
                    VoiceRuntimeCleanupDB.tenant_id,
                    VoiceRuntimeCleanupDB.owner_subject,
                    VoiceRuntimeCleanupDB.profile_id,
                )
                .limit(bounded_limit)
            ).all()
        return tuple((VoicePrincipal(tenant_id=row[0], subject=row[1]), row[2]) for row in rows)

    def get_source(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        source_session_id: str,
    ) -> VoiceRuntimeCleanupDB | None:
        with Session(engine) as session:
            return self._find_source(
                session,
                principal,
                profile_id=profile_id,
                source_session_id=source_session_id,
            )

    def activate_source(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        source_session_id: str,
        *,
        operation: str,
    ) -> bool:
        with Session(engine) as session:
            record = self._find_source(
                session,
                principal,
                profile_id=profile_id,
                source_session_id=source_session_id,
            )
            if record is None:
                return False
            record.operation = operation
            record.state = "pending"
            record.failure_reason_code = None
            record.updated_at = time.time()
            session.add(record)
            session.commit()
            return True

    def mark_attempt(self, principal: VoicePrincipal, profile_id: str, cleanup_id: str) -> bool:
        with Session(engine) as session:
            record = self._find_id(session, principal, profile_id=profile_id, cleanup_id=cleanup_id)
            if record is None:
                return False
            record.state = "pending"
            record.attempt_count += 1
            record.failure_reason_code = None
            record.updated_at = time.time()
            session.add(record)
            session.commit()
            return True

    def mark_failed(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        cleanup_id: str,
        *,
        reason_code: str,
    ) -> bool:
        with Session(engine) as session:
            record = self._find_id(session, principal, profile_id=profile_id, cleanup_id=cleanup_id)
            if record is None:
                return False
            record.state = "failed"
            record.failure_reason_code = reason_code
            record.updated_at = time.time()
            session.add(record)
            session.commit()
            return True

    def complete(self, principal: VoicePrincipal, profile_id: str, cleanup_id: str) -> bool:
        with Session(engine) as session:
            statement = delete(VoiceRuntimeCleanupDB).where(
                VoiceRuntimeCleanupDB.id == cleanup_id,
                *self._scope_predicates(principal, profile_id),
            )
            result = session.exec(statement)
            session.commit()
            return bool(result.rowcount)

    def status(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        *,
        include_provisional: bool = False,
    ) -> VoiceRuntimeCleanupStatus:
        rows = self.list_scope(principal, profile_id)
        if not include_provisional:
            rows = tuple(record for record in rows if record.state != "provisional")
        return VoiceRuntimeCleanupStatus(
            pending_count=len(rows),
            failed_count=sum(record.state == "failed" for record in rows),
        )

    @staticmethod
    def _scope_predicates(principal: VoicePrincipal, profile_id: str) -> tuple:
        return (
            VoiceRuntimeCleanupDB.tenant_id == principal.tenant_id,
            VoiceRuntimeCleanupDB.owner_subject == principal.subject,
            VoiceRuntimeCleanupDB.profile_id == profile_id,
        )

    @staticmethod
    def _find_source(
        session: Session,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        source_session_id: str,
    ) -> VoiceRuntimeCleanupDB | None:
        return session.exec(
            select(VoiceRuntimeCleanupDB).where(
                VoiceRuntimeCleanupDB.source_session_id == source_session_id,
                *VoiceRuntimeCleanupRepository._scope_predicates(principal, profile_id),
            )
        ).first()

    @staticmethod
    def _find_id(
        session: Session,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        cleanup_id: str,
    ) -> VoiceRuntimeCleanupDB | None:
        return session.exec(
            select(VoiceRuntimeCleanupDB).where(
                VoiceRuntimeCleanupDB.id == cleanup_id,
                *VoiceRuntimeCleanupRepository._scope_predicates(principal, profile_id),
            )
        ).first()
