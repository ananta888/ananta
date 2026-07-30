"""SQL adapter from persistent source grants to the enforcement ports."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.source_access_enforcement import (
    SourceAccessGrantConsumptionDB,
    SourceAccessGrantExecutionPolicyDB,
)
from agent.db_models.source_control import SourceAccessGrantDB
from agent.services.source_access_enforcement import (
    ResolvedSourceGrant,
    SourceAccessRequest,
    source_access_grant_digest,
)
from ananta_contracts.source_control import SourceAccessGrant


class PersistentSourceAccessAdapterError(ValueError):
    pass


class SQLSourceAccessEnforcementAdapter:
    """Resolve persisted grants and atomically consume one-time grants."""

    def __init__(
        self,
        engine: Any,
        *,
        allow_legacy_reusable_grants: bool = False,
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        self._engine = engine
        self._allow_legacy_reusable_grants = bool(
            allow_legacy_reusable_grants
        )
        self._clock = clock

    def bind_execution_policy(
        self,
        *,
        grant_id: str,
        grant_digest: str,
        destination_digest: str,
        consumption_mode: str,
        grant_lock_version: int = 1,
    ) -> None:
        if consumption_mode not in {"reusable", "one_time"}:
            raise PersistentSourceAccessAdapterError(
                "grant_consumption_mode_invalid"
            )
        now = self._aware_now()
        with Session(self._engine) as session:
            existing = session.get(
                SourceAccessGrantExecutionPolicyDB,
                str(grant_id),
            )
            if existing is not None:
                if (
                    existing.grant_digest != grant_digest
                    or existing.destination_digest
                    != destination_digest
                    or existing.consumption_mode != consumption_mode
                    or existing.grant_lock_version
                    != int(grant_lock_version)
                ):
                    raise PersistentSourceAccessAdapterError(
                        "grant_execution_policy_conflict"
                    )
                return
            session.add(
                SourceAccessGrantExecutionPolicyDB(
                    grant_id=str(grant_id),
                    grant_digest=str(grant_digest),
                    destination_digest=str(destination_digest),
                    consumption_mode=consumption_mode,
                    grant_lock_version=max(
                        1,
                        int(grant_lock_version),
                    ),
                    concurrency_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()

    def resolve_active(
        self,
        request: SourceAccessRequest,
    ) -> ResolvedSourceGrant | None:
        grant_id = str(request.source_access_grant_id or "")
        grant_digest = str(request.source_access_grant_digest or "")
        if not grant_id or not grant_digest:
            return None
        with Session(self._engine) as session:
            grant_row = session.exec(
                select(SourceAccessGrantDB).where(
                    SourceAccessGrantDB.grant_id == grant_id
                )
            ).first()
            if grant_row is None:
                return None
            grant = self._hydrate_grant(grant_row)
            if source_access_grant_digest(grant) != grant_digest:
                return None
            policy = session.get(
                SourceAccessGrantExecutionPolicyDB,
                grant_id,
            )
            if policy is None:
                if not self._allow_legacy_reusable_grants:
                    return None
                return ResolvedSourceGrant(
                    grant=grant,
                    consumption_mode="reusable",
                    concurrency_version=max(
                        1,
                        int(
                            getattr(grant_row, "lock_version", None)
                            or getattr(grant_row, "version", 1)
                        ),
                    ),
                )
            if (
                policy.grant_digest != grant_digest
                or policy.destination_digest
                != request.destination_digest
                or policy.grant_lock_version
                != max(
                    1,
                    int(
                        getattr(grant_row, "lock_version", None)
                        or getattr(grant_row, "version", 1)
                    ),
                )
            ):
                return None
            return ResolvedSourceGrant(
                grant=grant,
                consumption_mode=policy.consumption_mode,
                concurrency_version=policy.concurrency_version,
            )

    def consume_once(
        self,
        *,
        grant_id: str,
        expected_version: int,
        consumption_digest: str,
    ) -> bool:
        now = self._aware_now()
        try:
            with Session(self._engine) as session:
                policy = session.exec(
                    select(SourceAccessGrantExecutionPolicyDB)
                    .where(
                        SourceAccessGrantExecutionPolicyDB.grant_id
                        == str(grant_id)
                    )
                    .with_for_update()
                ).first()
                if (
                    policy is None
                    or policy.consumption_mode != "one_time"
                    or policy.concurrency_version != expected_version
                    or session.get(
                        SourceAccessGrantConsumptionDB,
                        str(grant_id),
                    )
                    is not None
                ):
                    return False
                grant_row = session.exec(
                    select(SourceAccessGrantDB)
                    .where(SourceAccessGrantDB.grant_id == str(grant_id))
                    .with_for_update()
                ).first()
                if (
                    grant_row is None
                    or policy.grant_lock_version
                    != max(
                        1,
                        int(
                            getattr(
                                grant_row,
                                "lock_version",
                                None,
                            )
                            or getattr(grant_row, "version", 1)
                        ),
                    )
                    or self._grant_row_inactive_or_expired(
                        grant_row,
                        now=now,
                    )
                ):
                    return False
                session.add(
                    SourceAccessGrantConsumptionDB(
                        grant_id=str(grant_id),
                        expected_version=expected_version,
                        consumption_digest=str(consumption_digest),
                        consumed_at=now,
                    )
                )
                policy.concurrency_version += 1
                policy.updated_at = now
                session.add(policy)
                session.commit()
                return True
        except IntegrityError:
            return False

    @staticmethod
    def _hydrate_grant(row: SourceAccessGrantDB) -> SourceAccessGrant:
        return SourceAccessGrant(
            schema="ananta.source-control.source-access-grant.v1",
            authority="hub",
            grant_id=row.grant_id,
            version=row.grant_version,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            source_revision_id=row.source_revision_id,
            destination_id=row.destination_id,
            operation=row.operation,
            transformation=row.transformation,
            purpose=row.purpose,
            policy_version=row.policy_version,
            policy_snapshot_digest=row.policy_snapshot_digest,
            state=row.state,
            issued_at=datetime.fromtimestamp(
                row.issued_at_epoch, tz=timezone.utc
            ),
            expires_at=datetime.fromtimestamp(
                row.expires_at_epoch, tz=timezone.utc
            ),
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise PersistentSourceAccessAdapterError(
                "grant_adapter_clock_must_be_aware"
            )
        return value.astimezone(timezone.utc)

    @staticmethod
    def _grant_row_inactive_or_expired(
        row: SourceAccessGrantDB,
        *,
        now: datetime,
    ) -> bool:
        raw_state = getattr(row, "state", "")
        state = str(getattr(raw_state, "value", raw_state)).lower()
        if state != "active":
            return True
        expires_at_epoch = getattr(row, "expires_at_epoch", None)
        if not isinstance(expires_at_epoch, (int, float)):
            return True
        return float(expires_at_epoch) <= now.timestamp()


__all__ = [
    "PersistentSourceAccessAdapterError",
    "SQLSourceAccessEnforcementAdapter",
]
