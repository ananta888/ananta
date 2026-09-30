"""SQL Audience snapshot retention: fenced tombstoning and content purge."""

from __future__ import annotations

import hashlib
from dataclasses import fields
from typing import Callable

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import SfuBroadcastAudienceDB, SfuFanoutRouteDB, SfuReceiverGroupDB
from agent.db_models.sfu_broadcast_retention import (
    SfuAudienceRetentionFenceDB,
    SfuAudienceSnapshotTombstoneDB,
)
from agent.models.sfu_broadcast_projection import (
    SfuAudienceRetentionFence,
    SfuAudienceRetentionPurgePage,
    SfuBroadcastAudience,
    SfuBroadcastRoomScope,
    SfuProjectionMutationResult,
)
from agent.repositories.sfu_broadcast_projection_paging import (
    _decode_retention_cursor,
    _encode_retention_cursor,
)
from agent.repositories.sfu_broadcast_projection_rules import (
    _LIVE_STATUSES,
    _TERMINAL_STATUSES,
    SfuBroadcastRepositoryError,
    _audience_tombstone_id,
    _result,
    _scope_digest,
    _validate_page_size,
    _validate_retention_fence,
)
from agent.repositories.sfu_broadcast_sql_repository import SqlSfuBroadcastAudienceRepository


class SqlSfuAudienceSnapshotRetentionRepository:
    def __init__(
        self,
        *,
        db_engine=default_engine,
        tombstone_retention_seconds: int = 30 * 86_400,
        audience_repository_factory: Callable[
            ..., SqlSfuBroadcastAudienceRepository
        ] = SqlSfuBroadcastAudienceRepository,
    ) -> None:
        self._engine = db_engine
        self._tombstone_retention_seconds = tombstone_retention_seconds
        self._audience_repository_factory = audience_repository_factory

    def tombstone(
        self,
        scope: SfuBroadcastRoomScope,
        projection_id: str,
        *,
        expected_version: int,
        retention_reason: str,
        purge_deadline: float,
        fence: SfuAudienceRetentionFence,
        now: float,
    ) -> SfuProjectionMutationResult[SfuBroadcastAudience]:
        _validate_retention_fence(fence, now)
        try:
            with Session(self._engine) as db:
                _claim_sql_retention_fence(db, fence, now)
                row = db.exec(select(SfuBroadcastAudienceDB).where(
                    SfuBroadcastAudienceDB.id == projection_id,
                    SfuBroadcastAudienceDB.tenant_id == scope.tenant_id,
                    SfuBroadcastAudienceDB.session_id == scope.session_id,
                )).first()
                if row is None:
                    tombstone = db.get(
                        SfuAudienceSnapshotTombstoneDB,
                        _audience_tombstone_id(scope, projection_id),
                    )
                    return _result(
                        "saved" if tombstone else "not_found",
                        replayed=tombstone is not None,
                        reason=None if tombstone else "projection_not_found",
                    )
                current = SfuBroadcastAudience(**{
                    field.name: getattr(row, field.name) for field in fields(SfuBroadcastAudience)
                })
                if row.status == "tombstoned":
                    return _result("saved", value=current, replayed=True)
                if row.version != expected_version:
                    return _result("conflict", value=current, reason="projection_version_conflict")
                if purge_deadline < now or purge_deadline < row.expires_at:
                    return _result("conflict", value=current, reason="retention_deadline_invalid")
                if _sql_live_route_exists(db, row.id, now):
                    return _result("conflict", value=current, reason="audience_snapshot_route_active")
                result = db.exec(sa.update(SfuBroadcastAudienceDB).where(
                    SfuBroadcastAudienceDB.id == projection_id,
                    SfuBroadcastAudienceDB.version == expected_version,
                ).values(
                    status="tombstoned", retention_status="purge_pending",
                    retain_until=purge_deadline, tombstoned_at=now,
                    tombstone_reason=retention_reason, fencing_token=fence.fencing_token,
                    version=SfuBroadcastAudienceDB.version + 1,
                    request_digest=hashlib.sha256(
                        f"retention\0{row.request_digest}\0{retention_reason}\0{expected_version}".encode()
                    ).hexdigest(),
                    idempotency_key_digest=hashlib.sha256(
                        f"retention\0{projection_id}\0{retention_reason}".encode()
                    ).hexdigest(),
                    updated_at=now, audited_at=now,
                ))
                if int(result.rowcount or 0) != 1:
                    db.rollback()
                    return _result("conflict", value=current, reason="projection_version_conflict")
                db.commit()
                saved = self._audience_repository_factory(db_engine=self._engine).get(
                    scope, projection_id
                )
                return _result("saved", value=saved)
        except SfuBroadcastRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise SfuBroadcastRepositoryError("audience_retention_store_unavailable") from exc

    def purge_due(
        self,
        *,
        fence: SfuAudienceRetentionFence,
        now: float,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuAudienceRetentionPurgePage:
        _validate_retention_fence(fence, now)
        _validate_page_size(page_size, 1000)
        after = _decode_retention_cursor(cursor) if cursor else None
        try:
            with Session(self._engine) as db:
                _claim_sql_retention_fence(db, fence, now)
                query = select(SfuBroadcastAudienceDB).where(
                    SfuBroadcastAudienceDB.status == "tombstoned",
                    SfuBroadcastAudienceDB.retention_status == "purge_pending",
                    SfuBroadcastAudienceDB.retain_until <= now,
                )
                if after is not None:
                    query = query.where(sa.or_(
                        SfuBroadcastAudienceDB.retain_until > after[0],
                        sa.and_(
                            SfuBroadcastAudienceDB.retain_until == after[0],
                            SfuBroadcastAudienceDB.id > after[1],
                        ),
                    ))
                rows = db.exec(query.order_by(
                    SfuBroadcastAudienceDB.retain_until,
                    SfuBroadcastAudienceDB.id,
                ).limit(page_size + 1)).all()
                selected = rows[:page_size]
                purged = 0
                for audience in selected:
                    if _sql_live_route_exists(db, audience.id, now):
                        continue
                    routes = db.exec(select(SfuFanoutRouteDB).where(
                        SfuFanoutRouteDB.audience_projection_id == audience.id
                    )).all()
                    if any(route.status not in _TERMINAL_STATUSES for route in routes):
                        continue
                    group_ids = {route.receiver_group_projection_id for route in routes}
                    for route in routes:
                        db.delete(route)
                    db.flush()
                    for group_id in group_ids:
                        reference = db.exec(select(SfuFanoutRouteDB.id).where(
                            SfuFanoutRouteDB.receiver_group_projection_id == group_id
                        ).limit(1)).first()
                        group = db.get(SfuReceiverGroupDB, group_id)
                        if reference is None and group is not None and group.status in _TERMINAL_STATUSES:
                            db.delete(group)
                    tombstone_id = _audience_tombstone_id(
                        SfuBroadcastRoomScope(audience.tenant_id, audience.session_id), audience.id
                    )
                    if db.get(SfuAudienceSnapshotTombstoneDB, tombstone_id) is None:
                        db.add(SfuAudienceSnapshotTombstoneDB(
                            id=tombstone_id,
                            scope_digest=_scope_digest(audience.tenant_id, audience.session_id),
                            final_version=audience.version,
                            reason_code=audience.tombstone_reason or "retention_expired",
                            fencing_token=fence.fencing_token,
                            purged_at=now,
                            deny_until=now + self._tombstone_retention_seconds,
                        ))
                    db.delete(audience)
                    purged += 1
                db.commit()
                next_cursor = (
                    _encode_retention_cursor((selected[-1].retain_until, selected[-1].id))
                    if len(rows) > page_size and selected else None
                )
                return SfuAudienceRetentionPurgePage(purged, next_cursor)
        except SfuBroadcastRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise SfuBroadcastRepositoryError("audience_retention_store_unavailable") from exc


def _claim_sql_retention_fence(
    db: Session, fence: SfuAudienceRetentionFence, now: float,
) -> None:
    row = db.get(SfuAudienceRetentionFenceDB, "global")
    if row is None:
        db.add(SfuAudienceRetentionFenceDB(
            id="global", owner_id=fence.owner_id, fencing_token=fence.fencing_token,
            lease_expires_at=fence.lease_expires_at, version=1, updated_at=now,
        ))
        db.flush()
        return
    if row.fencing_token > fence.fencing_token:
        raise SfuBroadcastRepositoryError("audience_retention_fence_stale")
    if (
        row.fencing_token == fence.fencing_token and row.owner_id != fence.owner_id
        and row.lease_expires_at > now
    ):
        raise SfuBroadcastRepositoryError("audience_retention_lease_conflict")
    result = db.exec(sa.update(SfuAudienceRetentionFenceDB).where(
        SfuAudienceRetentionFenceDB.id == "global",
        SfuAudienceRetentionFenceDB.version == row.version,
    ).values(
        owner_id=fence.owner_id, fencing_token=fence.fencing_token,
        lease_expires_at=fence.lease_expires_at,
        version=SfuAudienceRetentionFenceDB.version + 1, updated_at=now,
    ))
    if int(result.rowcount or 0) != 1:
        raise SfuBroadcastRepositoryError("audience_retention_lease_conflict")


def _sql_live_route_exists(db: Session, audience_id: str, now: float) -> bool:
    return db.exec(select(SfuFanoutRouteDB.id).where(
        SfuFanoutRouteDB.audience_projection_id == audience_id,
        SfuFanoutRouteDB.status.in_(tuple(_LIVE_STATUSES)),
        SfuFanoutRouteDB.expires_at > now,
    ).limit(1)).first() is not None
