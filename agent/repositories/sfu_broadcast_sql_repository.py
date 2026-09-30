"""SQL SFU broadcast projection adapters for Audiences, Receiver Groups and Fanout Routes."""

from __future__ import annotations

import time
from dataclasses import asdict, fields, replace
from typing import Callable, Generic

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import SfuBroadcastAudienceDB, SfuFanoutRouteDB, SfuReceiverGroupDB
from agent.db_models.sfu_broadcast_retention import (
    SfuAudienceSnapshotTombstoneDB,
)
from agent.models.sfu_broadcast_projection import (
    SfuAtomicGroupProjectionMutation,
    SfuBroadcastAudience,
    SfuBroadcastRoomScope,
    SfuFanoutRoute,
    SfuProjectionMutation,
    SfuProjectionMutationResult,
    SfuProjectionPage,
    SfuReceiverGroup,
)
from agent.repositories.sfu_broadcast_projection_paging import (
    _decode_cursor,
    _encode_cursor,
    _page_key,
)
from agent.repositories.sfu_broadcast_projection_rules import (
    _LIVE_STATUSES,
    _TERMINAL_STATUSES,
    ProjectionT,
    RowT,
    SfuBroadcastRepositoryError,
    _audience_tombstone_id,
    _digest,
    _effective_now,
    _expiry_request_digest,
    _prepare_mutation,
    _result,
    _sql_error_result,
    _validate_atomic_group_mutation,
    _validate_atomic_group_parent,
    _validate_identifier,
    _validate_mutation,
    _validate_mutation_fence,
    _validate_page_size,
    _validate_page_size_max,
    _validate_scope,
)


class _SqlProjectionRepository(Generic[ProjectionT, RowT]):
    def __init__(
        self,
        *,
        model: type[RowT],
        domain: type[ProjectionT],
        sort_attribute: str,
        natural_attributes: tuple[str, ...],
        epoch_attributes: tuple[str, ...],
        db_engine,
        page_size_max: int,
        clock: Callable[[], float],
    ) -> None:
        _validate_page_size_max(page_size_max)
        self._model = model
        self._domain = domain
        self._sort_attribute = sort_attribute
        self._natural_attributes = natural_attributes
        self._epoch_attributes = epoch_attributes
        self._engine = db_engine
        self._page_size_max = page_size_max
        self._clock = clock

    def get(self, scope: SfuBroadcastRoomScope, projection_id: str) -> ProjectionT | None:
        _validate_scope(scope)
        _validate_identifier(projection_id, "projection_id")
        with Session(self._engine) as db:
            row = self._select_scoped(db, scope, projection_id)
            return self._from_row(row) if row is not None else None

    def save(
        self,
        mutation: SfuProjectionMutation[ProjectionT],
        *,
        now: float | None = None,
    ) -> SfuProjectionMutationResult[ProjectionT]:
        effective_now = _effective_now(now, self._clock)
        _validate_mutation(mutation)
        scope = mutation.value.scope
        try:
            with Session(self._engine) as db:
                current_row = self._select_scoped(db, scope, mutation.value.id)
                current = self._from_row(current_row) if current_row is not None else None
                if self._model is SfuBroadcastAudienceDB and current is None:
                    tombstone = db.get(
                        SfuAudienceSnapshotTombstoneDB,
                        _audience_tombstone_id(scope, mutation.value.id),
                    )
                    if tombstone is not None:
                        return _result("conflict", reason="audience_snapshot_tombstoned")
                outcome, saved = _prepare_mutation(
                    mutation,
                    current=current,
                    now=effective_now,
                    epoch_attributes=self._epoch_attributes,
                )
                if outcome is not None:
                    return outcome
                assert saved is not None
                relationship = self._validate_relationship(db, saved)
                if relationship is not None:
                    return relationship
                if self._has_natural_conflict(db, saved):
                    return _result("conflict", reason="active_projection_conflict")
                if current is None:
                    db.add(self._model(**asdict(saved)))
                else:
                    values = asdict(saved)
                    values.pop("id")
                    updated = db.exec(
                        sa.update(self._model)
                        .where(
                            self._model.id == saved.id,
                            self._model.tenant_id == saved.tenant_id,
                            self._model.session_id == saved.session_id,
                            self._model.version == current.version,
                        )
                        .values(**values)
                    )
                    if int(getattr(updated, "rowcount", 0) or 0) != 1:
                        db.rollback()
                        return _result("conflict", reason="projection_version_conflict")
                try:
                    db.commit()
                    return _result("saved", value=saved)
                except IntegrityError as error:
                    db.rollback()
                    return self._integrity_result(db, mutation, error)
        except SQLAlchemyError as error:
            return _sql_error_result(error)

    def expire(
        self,
        scope: SfuBroadcastRoomScope,
        projection_id: str,
        *,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
        now: float | None = None,
    ) -> SfuProjectionMutationResult[ProjectionT]:
        effective_now = _effective_now(now, self._clock)
        _validate_mutation_fence(expected_version, idempotency_key)
        current = self.get(scope, projection_id)
        if current is None:
            return _result("not_found", reason="projection_not_found")
        if current.status in _TERMINAL_STATUSES:
            return _result("saved", value=current, replayed=True)
        if current.expires_at > effective_now:
            return _result("conflict", value=current, reason="projection_not_expired")
        desired = replace(
            current,
            status="expired",
            retention_status="retained",
            request_digest=_expiry_request_digest(current, effective_now),
        )
        return self.save(
            SfuProjectionMutation(
                desired,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            ),
            now=effective_now,
        )

    def page(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[ProjectionT]:
        return self._page(scope, mode="all", page_size=page_size, cursor=cursor)

    def page_expired(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        now: float,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[ProjectionT]:
        return self._page(
            scope,
            mode="expired",
            page_size=page_size,
            cursor=cursor,
            threshold=float(now),
        )

    def page_reconciliation(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        current_room_state_revision: int,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[ProjectionT]:
        if current_room_state_revision < 1:
            raise SfuBroadcastRepositoryError("room_state_revision_invalid")
        return self._page(
            scope,
            mode="reconciliation",
            page_size=page_size,
            cursor=cursor,
            threshold=current_room_state_revision,
        )

    def _page_retention_due(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        now: float,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[ProjectionT]:
        return self._page(
            scope,
            mode="retention",
            page_size=page_size,
            cursor=cursor,
            threshold=float(now),
        )

    def _page(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        mode: str,
        page_size: int,
        cursor: str | None,
        threshold: float | int | None = None,
    ) -> SfuProjectionPage[ProjectionT]:
        _validate_scope(scope)
        _validate_page_size(page_size, self._page_size_max)
        after = _decode_cursor(cursor, mode) if cursor is not None else None
        with Session(self._engine) as db:
            statement = select(self._model).where(
                self._model.tenant_id == scope.tenant_id,
                self._model.session_id == scope.session_id,
            )
            order_column = getattr(self._model, self._sort_attribute)
            if mode == "expired":
                order_column = self._model.expires_at
                statement = statement.where(
                    self._model.status.in_(_LIVE_STATUSES),
                    self._model.expires_at <= threshold,
                )
            elif mode == "reconciliation":
                order_column = self._model.room_state_revision
                statement = statement.where(self._model.room_state_revision < threshold)
            elif mode == "retention":
                order_column = self._model.retain_until
                statement = statement.where(
                    self._model.retain_until <= threshold,
                    self._model.retention_status != "purged",
                )
            if after is not None:
                first, row_id = after
                statement = statement.where(
                    sa.or_(
                        order_column > first,
                        sa.and_(order_column == first, self._model.id > row_id),
                    )
                )
            rows = db.exec(
                statement.order_by(order_column, self._model.id).limit(page_size + 1)
            ).all()
            included = rows[:page_size]
            items = tuple(self._from_row(row) for row in included)
            next_cursor = (
                _encode_cursor(
                    mode,
                    _page_key(items[-1], mode, self._sort_attribute),
                )
                if len(rows) > page_size and items
                else None
            )
            return SfuProjectionPage(items, next_cursor)

    def _select_scoped(
        self,
        db: Session,
        scope: SfuBroadcastRoomScope,
        projection_id: str,
    ):
        return db.exec(
            select(self._model).where(
                self._model.id == projection_id,
                self._model.tenant_id == scope.tenant_id,
                self._model.session_id == scope.session_id,
            )
        ).first()

    def _from_row(self, row) -> ProjectionT:
        return self._domain(**{
            field.name: getattr(row, field.name)
            for field in fields(self._domain)
        })

    def _has_natural_conflict(self, db: Session, candidate: ProjectionT) -> bool:
        if candidate.status != "active" or candidate.tombstoned_at is not None:
            return False
        statement = select(self._model.id).where(
            self._model.tenant_id == candidate.tenant_id,
            self._model.session_id == candidate.session_id,
            self._model.id != candidate.id,
            self._model.status == "active",
            self._model.tombstoned_at.is_(None),
        )
        for attribute in self._natural_attributes:
            statement = statement.where(
                getattr(self._model, attribute) == getattr(candidate, attribute)
            )
        return db.exec(statement.limit(1)).first() is not None

    def _validate_relationship(
        self,
        _db: Session,
        _candidate: ProjectionT,
    ) -> SfuProjectionMutationResult[ProjectionT] | None:
        return None

    def _integrity_result(
        self,
        db: Session,
        mutation: SfuProjectionMutation[ProjectionT],
        error: IntegrityError,
    ) -> SfuProjectionMutationResult[ProjectionT]:
        message = str(error.orig).lower()
        if "expired" in message:
            return _result("expired", reason="projection_expired")
        if "non_monotone" in message or "stale_or_orphan" in message:
            return _result("stale_epoch", reason="projection_epoch_stale")
        current_row = self._select_scoped(db, mutation.value.scope, mutation.value.id)
        if current_row is not None and mutation.idempotency_key:
            current = self._from_row(current_row)
            digest = _digest(mutation.idempotency_key)
            if current.idempotency_key_digest == digest:
                if current.request_digest == mutation.value.request_digest:
                    return _result("saved", value=current, replayed=True)
                return _result("conflict", value=current, reason="idempotency_conflict")
        if "foreign key" in message or "orphan" in message:
            return _result("not_found", reason="projection_reference_not_found")
        return _result("conflict", reason="projection_write_conflict")


class SqlSfuBroadcastAudienceRepository(
    _SqlProjectionRepository[SfuBroadcastAudience, SfuBroadcastAudienceDB]
):
    def __init__(
        self,
        *,
        db_engine=default_engine,
        page_size_max: int = 200,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(
            model=SfuBroadcastAudienceDB,
            domain=SfuBroadcastAudience,
            sort_attribute="audience_ref",
            natural_attributes=("publication_ref",),
            epoch_attributes=("policy_epoch", "membership_epoch", "key_epoch"),
            db_engine=db_engine,
            page_size_max=page_size_max,
            clock=clock,
        )

    def page_retention_due(
        self,
        scope: SfuBroadcastRoomScope,
        *,
        now: float,
        page_size: int,
        cursor: str | None = None,
    ) -> SfuProjectionPage[SfuBroadcastAudience]:
        return self._page_retention_due(
            scope, now=now, page_size=page_size, cursor=cursor
        )


class SqlSfuReceiverGroupRepository(
    _SqlProjectionRepository[SfuReceiverGroup, SfuReceiverGroupDB]
):
    def __init__(
        self,
        *,
        db_engine=default_engine,
        page_size_max: int = 200,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(
            model=SfuReceiverGroupDB,
            domain=SfuReceiverGroup,
            sort_attribute="receiver_group_ref",
            natural_attributes=("subscription_ref",),
            epoch_attributes=("membership_epoch", "key_epoch", "topology_epoch"),
            db_engine=db_engine,
            page_size_max=page_size_max,
            clock=clock,
        )


class SqlSfuAtomicGroupProjectionRepository:
    """SQL transaction joining authoritative Audience epochs and Group CAS."""

    def __init__(
        self,
        *,
        db_engine=default_engine,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._engine = db_engine
        self._clock = clock

    def save_authorized(
        self,
        mutation: SfuAtomicGroupProjectionMutation,
        *,
        now: float | None = None,
    ) -> SfuProjectionMutationResult[SfuReceiverGroup]:
        effective_now = _effective_now(now, self._clock)
        _validate_atomic_group_mutation(mutation)
        desired = mutation.mutation.value
        try:
            with Session(self._engine) as db:
                group_row = db.exec(
                    select(SfuReceiverGroupDB)
                    .where(
                        SfuReceiverGroupDB.id == desired.id,
                        SfuReceiverGroupDB.tenant_id == desired.tenant_id,
                        SfuReceiverGroupDB.session_id == desired.session_id,
                    )
                    .with_for_update()
                ).first()
                current = (
                    _receiver_group_from_row(group_row)
                    if group_row is not None
                    else None
                )
                outcome, saved = _prepare_mutation(
                    mutation.mutation,
                    current=current,
                    now=effective_now,
                    epoch_attributes=(
                        "membership_epoch",
                        "key_epoch",
                        "topology_epoch",
                    ),
                )
                if outcome is not None:
                    return outcome
                assert saved is not None
                parent_row = db.exec(
                    select(SfuBroadcastAudienceDB)
                    .where(
                        SfuBroadcastAudienceDB.id
                        == mutation.audience_projection_id,
                        SfuBroadcastAudienceDB.tenant_id == saved.tenant_id,
                        SfuBroadcastAudienceDB.session_id == saved.session_id,
                    )
                    .with_for_update()
                ).first()
                parent = (
                    _audience_from_row(parent_row)
                    if parent_row is not None
                    else None
                )
                parent_error = _validate_atomic_group_parent(
                    mutation,
                    parent=parent,
                    current=current,
                    saved=saved,
                    now=effective_now,
                )
                if parent_error is not None:
                    return parent_error
                natural_conflict = db.exec(
                    select(SfuReceiverGroupDB.id).where(
                        SfuReceiverGroupDB.tenant_id == saved.tenant_id,
                        SfuReceiverGroupDB.session_id == saved.session_id,
                        SfuReceiverGroupDB.id != saved.id,
                        SfuReceiverGroupDB.status == "active",
                        SfuReceiverGroupDB.tombstoned_at.is_(None),
                        SfuReceiverGroupDB.subscription_ref
                        == saved.subscription_ref,
                    )
                ).first()
                if natural_conflict is not None:
                    return _result("conflict", reason="active_projection_conflict")
                if current is None:
                    db.add(SfuReceiverGroupDB(**asdict(saved)))
                else:
                    values = asdict(saved)
                    values.pop("id")
                    updated = db.exec(
                        sa.update(SfuReceiverGroupDB)
                        .where(
                            SfuReceiverGroupDB.id == saved.id,
                            SfuReceiverGroupDB.tenant_id == saved.tenant_id,
                            SfuReceiverGroupDB.session_id == saved.session_id,
                            SfuReceiverGroupDB.version == current.version,
                        )
                        .values(**values)
                    )
                    if int(getattr(updated, "rowcount", 0) or 0) != 1:
                        db.rollback()
                        return _result(
                            "conflict", reason="projection_version_conflict"
                        )
                try:
                    db.commit()
                    return _result("saved", value=saved)
                except IntegrityError as error:
                    db.rollback()
                    return _sql_error_result(error)
        except SQLAlchemyError as error:
            return _sql_error_result(error)


class SqlSfuFanoutRouteRepository(
    _SqlProjectionRepository[SfuFanoutRoute, SfuFanoutRouteDB]
):
    def __init__(
        self,
        *,
        db_engine=default_engine,
        page_size_max: int = 200,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(
            model=SfuFanoutRouteDB,
            domain=SfuFanoutRoute,
            sort_attribute="route_ref",
            natural_attributes=("publication_ref", "subscription_ref"),
            epoch_attributes=(
                "policy_epoch",
                "membership_epoch",
                "key_epoch",
                "route_epoch",
                "topology_epoch",
            ),
            db_engine=db_engine,
            page_size_max=page_size_max,
            clock=clock,
        )

    def _validate_relationship(
        self,
        db: Session,
        candidate: SfuFanoutRoute,
    ) -> SfuProjectionMutationResult[SfuFanoutRoute] | None:
        if candidate.status in _TERMINAL_STATUSES:
            return None
        audience = db.exec(
            select(SfuBroadcastAudienceDB).where(
                SfuBroadcastAudienceDB.id == candidate.audience_projection_id,
                SfuBroadcastAudienceDB.tenant_id == candidate.tenant_id,
                SfuBroadcastAudienceDB.session_id == candidate.session_id,
            )
        ).first()
        group = db.exec(
            select(SfuReceiverGroupDB).where(
                SfuReceiverGroupDB.id == candidate.receiver_group_projection_id,
                SfuReceiverGroupDB.tenant_id == candidate.tenant_id,
                SfuReceiverGroupDB.session_id == candidate.session_id,
            )
        ).first()
        if audience is None or group is None:
            return _result("not_found", reason="route_projection_parent_not_found")
        if (
            audience.status != "active"
            or group.status != "active"
            or audience.publication_ref != candidate.publication_ref
            or group.subscription_ref != candidate.subscription_ref
            or audience.room_state_revision != candidate.room_state_revision
            or group.room_state_revision != candidate.room_state_revision
        ):
            return _result("stale_epoch", reason="route_projection_parent_stale")
        return None


def _audience_from_row(row: SfuBroadcastAudienceDB) -> SfuBroadcastAudience:
    return SfuBroadcastAudience(
        **{field.name: getattr(row, field.name) for field in fields(SfuBroadcastAudience)}
    )


def _receiver_group_from_row(row: SfuReceiverGroupDB) -> SfuReceiverGroup:
    return SfuReceiverGroup(
        **{field.name: getattr(row, field.name) for field in fields(SfuReceiverGroup)}
    )
