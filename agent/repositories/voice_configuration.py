from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from sqlmodel import Session, select, update

from agent.database import engine
from agent.db_models import VoiceConfigurationDeltaDB, VoiceGovernanceIdempotencyDB
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal


class VoiceConfigurationRepository:
    """Tenant-scoped globals plus subject-scoped profile/session deltas."""

    _TENANT_GLOBAL_OWNER = "__tenant_global__"

    def get(self, principal: VoicePrincipal, *, scope: str, scope_id: str) -> VoiceConfigurationDeltaDB | None:
        owner_subject = self._owner_subject(principal, scope)
        with Session(engine) as session:
            record = session.exec(
                select(VoiceConfigurationDeltaDB).where(
                    VoiceConfigurationDeltaDB.tenant_id == principal.tenant_id,
                    VoiceConfigurationDeltaDB.owner_subject == owner_subject,
                    VoiceConfigurationDeltaDB.scope == scope,
                    VoiceConfigurationDeltaDB.scope_id == scope_id,
                )
            ).first()
            if record is not None or scope != "global":
                return record
            # Read compatibility for pre-tenant-global records written by the
            # same principal before the shared owner sentinel was introduced.
            return session.exec(
                select(VoiceConfigurationDeltaDB).where(
                    VoiceConfigurationDeltaDB.tenant_id == principal.tenant_id,
                    VoiceConfigurationDeltaDB.owner_subject == principal.subject,
                    VoiceConfigurationDeltaDB.scope == scope,
                    VoiceConfigurationDeltaDB.scope_id == scope_id,
                )
            ).first()

    def put(
        self,
        principal: VoicePrincipal,
        *,
        scope: str,
        scope_id: str,
        delta: dict,
        expected_version: int | None,
        idempotency_record_id: str,
        idempotency_lease_token: float,
        result_builder: Callable[[VoiceConfigurationDeltaDB], dict[str, Any]],
    ) -> tuple[VoiceConfigurationDeltaDB, dict[str, Any]]:
        """Commit the configuration delta and fenced replay result atomically."""

        owner_subject = self._owner_subject(principal, scope)
        with Session(engine) as session:
            record = session.exec(
                select(VoiceConfigurationDeltaDB)
                .where(
                    VoiceConfigurationDeltaDB.tenant_id == principal.tenant_id,
                    VoiceConfigurationDeltaDB.owner_subject == owner_subject,
                    VoiceConfigurationDeltaDB.scope == scope,
                    VoiceConfigurationDeltaDB.scope_id == scope_id,
                )
                .with_for_update()
            ).first()
            if record is None:
                if expected_version not in {None, 0}:
                    raise VoiceGovernanceError(
                        code="voice_configuration.version_conflict",
                        message="voice configuration version does not match",
                        status_code=409,
                    )
                record = VoiceConfigurationDeltaDB(
                    tenant_id=principal.tenant_id,
                    owner_subject=owner_subject,
                    scope=scope,
                    scope_id=scope_id,
                    delta=dict(delta),
                )
            else:
                if expected_version is not None and record.version != expected_version:
                    raise VoiceGovernanceError(
                        code="voice_configuration.version_conflict",
                        message="voice configuration version does not match",
                        status_code=409,
                    )
                record.delta = dict(delta)
                record.version += 1
                record.updated_at = time.time()
            session.add(record)
            session.flush()
            result = dict(result_builder(record))
            self._complete_idempotency_claim(
                session,
                principal,
                record_id=idempotency_record_id,
                lease_token=idempotency_lease_token,
                result_metadata=result,
            )
            session.commit()
            session.refresh(record)
            return record, result

    @staticmethod
    def _complete_idempotency_claim(
        session: Session,
        principal: VoicePrincipal,
        *,
        record_id: str,
        lease_token: float,
        result_metadata: dict[str, Any],
    ) -> None:
        now = time.time()
        completed = session.exec(
            update(VoiceGovernanceIdempotencyDB)
            .where(
                VoiceGovernanceIdempotencyDB.id == record_id,
                VoiceGovernanceIdempotencyDB.tenant_id == principal.tenant_id,
                VoiceGovernanceIdempotencyDB.owner_subject == principal.subject,
                VoiceGovernanceIdempotencyDB.state == "pending",
                VoiceGovernanceIdempotencyDB.lease_expires_at == lease_token,
            )
            .values(
                state="completed",
                lease_expires_at=now,
                result_metadata=dict(result_metadata),
                updated_at=now,
            )
        )
        if completed.rowcount != 1:
            session.rollback()
            raise VoiceGovernanceError(
                code="voice_governance.stale_idempotency_claim",
                message="idempotency claim ownership changed before configuration mutation",
                status_code=409,
            )

    @classmethod
    def _owner_subject(cls, principal: VoicePrincipal, scope: str) -> str:
        return cls._TENANT_GLOBAL_OWNER if scope == "global" else principal.subject
