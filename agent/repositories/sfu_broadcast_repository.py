"""Substitutable SQL and in-memory adapters for SFU broadcast projections."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from dataclasses import asdict, fields, replace
from typing import Callable, Generic, TypeVar, cast

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import SfuBroadcastAudienceDB, SfuFanoutRouteDB, SfuReceiverGroupDB
from agent.services.sfu_broadcast_repository_ports import (
    SfuBroadcastAudience,
    SfuBroadcastRoomScope,
    SfuFanoutRoute,
    SfuProjectionEnvelope,
    SfuProjectionMutation,
    SfuProjectionMutationResult,
    SfuProjectionPage,
    SfuReceiverGroup,
)


ProjectionT = TypeVar("ProjectionT", bound=SfuProjectionEnvelope)
RowT = TypeVar("RowT")
_LIVE_STATUSES = frozenset({"pending", "active", "draining"})
_TERMINAL_STATUSES = frozenset({"expired", "revoked", "tombstoned"})
_STATUSES = _LIVE_STATUSES | _TERMINAL_STATUSES
_RETENTION_STATUSES = frozenset({"live", "retained", "purge_pending", "purged"})


class SfuBroadcastRepositoryError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class InMemorySfuBroadcastRepositoryStore:
    """Shareable state makes fresh adapters equivalent to a Hub restart."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.audiences: dict[tuple[str, str, str], SfuBroadcastAudience] = {}
        self.groups: dict[tuple[str, str, str], SfuReceiverGroup] = {}
        self.routes: dict[tuple[str, str, str], SfuFanoutRoute] = {}


class _InMemoryProjectionRepository(Generic[ProjectionT]):
    def __init__(
        self,
        *,
        store: InMemorySfuBroadcastRepositoryStore,
        items: Callable[
            [InMemorySfuBroadcastRepositoryStore],
            dict[tuple[str, str, str], ProjectionT],
        ],
        sort_attribute: str,
        natural_attributes: tuple[str, ...],
        epoch_attributes: tuple[str, ...],
        page_size_max: int,
        clock: Callable[[], float],
    ) -> None:
        _validate_page_size_max(page_size_max)
        self._store = store
        self._items_getter = items
        self._sort_attribute = sort_attribute
        self._natural_attributes = natural_attributes
        self._epoch_attributes = epoch_attributes
        self._page_size_max = page_size_max
        self._clock = clock

    def get(self, scope: SfuBroadcastRoomScope, projection_id: str) -> ProjectionT | None:
        _validate_scope(scope)
        _validate_identifier(projection_id, "projection_id")
        with self._store.lock:
            return self._items().get(_key(scope, projection_id))

    def save(
        self,
        mutation: SfuProjectionMutation[ProjectionT],
        *,
        now: float | None = None,
    ) -> SfuProjectionMutationResult[ProjectionT]:
        effective_now = _effective_now(now, self._clock)
        _validate_mutation(mutation)
        scope = mutation.value.scope
        key = _key(scope, mutation.value.id)
        with self._store.lock:
            items = self._items()
            current = items.get(key)
            outcome, saved = _prepare_mutation(
                mutation,
                current=current,
                now=effective_now,
                epoch_attributes=self._epoch_attributes,
            )
            if outcome is not None:
                return outcome
            assert saved is not None
            relationship = self._validate_relationship(saved)
            if relationship is not None:
                return relationship
            if current is None and any(value.id == saved.id for value in items.values()):
                return _result("conflict", reason="projection_id_conflict")
            if self._has_natural_conflict(items.values(), saved):
                return _result("conflict", reason="active_projection_conflict")
            items[key] = saved
            return _result("saved", value=saved)

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
        current = self.get(scope, projection_id)
        return self._expire_current(
            current,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
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
        with self._store.lock:
            values = [
                value
                for value in self._items().values()
                if value.tenant_id == scope.tenant_id and value.session_id == scope.session_id
            ]
            values = _filter_page(values, mode=mode, threshold=threshold)
            ordered = sorted(values, key=lambda value: _page_key(value, mode, self._sort_attribute))
            if after is not None:
                ordered = [
                    value
                    for value in ordered
                    if _page_key(value, mode, self._sort_attribute) > after
                ]
            selected = ordered[: page_size + 1]
            items = tuple(selected[:page_size])
            next_cursor = (
                _encode_cursor(mode, _page_key(items[-1], mode, self._sort_attribute))
                if len(selected) > page_size and items
                else None
            )
            return SfuProjectionPage(items, next_cursor)

    def _expire_current(
        self,
        current: ProjectionT | None,
        *,
        expected_version: int | None,
        idempotency_key: str | None,
        now: float,
    ) -> SfuProjectionMutationResult[ProjectionT]:
        _validate_mutation_fence(expected_version, idempotency_key)
        if current is None:
            return _result("not_found", reason="projection_not_found")
        if current.status in _TERMINAL_STATUSES:
            return _result("saved", value=current, replayed=True)
        if current.expires_at > now:
            return _result("conflict", value=current, reason="projection_not_expired")
        desired = replace(
            current,
            status="expired",
            retention_status="retained",
            request_digest=_expiry_request_digest(current, now),
        )
        return self.save(
            SfuProjectionMutation(
                desired,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            ),
            now=now,
        )

    def _items(self) -> dict[tuple[str, str, str], ProjectionT]:
        return self._items_getter(self._store)

    def _has_natural_conflict(
        self,
        values,
        candidate: ProjectionT,
    ) -> bool:
        if candidate.status != "active" or candidate.tombstoned_at is not None:
            return False
        natural = tuple(getattr(candidate, name) for name in self._natural_attributes)
        return any(
            value.id != candidate.id
            and value.tenant_id == candidate.tenant_id
            and value.session_id == candidate.session_id
            and value.status == "active"
            and value.tombstoned_at is None
            and tuple(getattr(value, name) for name in self._natural_attributes) == natural
            for value in values
        )

    def _validate_relationship(
        self,
        _candidate: ProjectionT,
    ) -> SfuProjectionMutationResult[ProjectionT] | None:
        return None


class InMemorySfuBroadcastAudienceRepository(
    _InMemoryProjectionRepository[SfuBroadcastAudience]
):
    def __init__(
        self,
        *,
        store: InMemorySfuBroadcastRepositoryStore | None = None,
        page_size_max: int = 200,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(
            store=store or InMemorySfuBroadcastRepositoryStore(),
            items=lambda state: state.audiences,
            sort_attribute="audience_ref",
            natural_attributes=("publication_ref",),
            epoch_attributes=("policy_epoch", "membership_epoch", "key_epoch"),
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


class InMemorySfuReceiverGroupRepository(_InMemoryProjectionRepository[SfuReceiverGroup]):
    def __init__(
        self,
        *,
        store: InMemorySfuBroadcastRepositoryStore | None = None,
        page_size_max: int = 200,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(
            store=store or InMemorySfuBroadcastRepositoryStore(),
            items=lambda state: state.groups,
            sort_attribute="receiver_group_ref",
            natural_attributes=("subscription_ref",),
            epoch_attributes=("membership_epoch", "key_epoch", "topology_epoch"),
            page_size_max=page_size_max,
            clock=clock,
        )


class InMemorySfuFanoutRouteRepository(_InMemoryProjectionRepository[SfuFanoutRoute]):
    def __init__(
        self,
        *,
        store: InMemorySfuBroadcastRepositoryStore | None = None,
        page_size_max: int = 200,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(
            store=store or InMemorySfuBroadcastRepositoryStore(),
            items=lambda state: state.routes,
            sort_attribute="route_ref",
            natural_attributes=("publication_ref", "subscription_ref"),
            epoch_attributes=(
                "policy_epoch",
                "membership_epoch",
                "key_epoch",
                "route_epoch",
                "topology_epoch",
            ),
            page_size_max=page_size_max,
            clock=clock,
        )

    def _validate_relationship(
        self,
        candidate: SfuFanoutRoute,
    ) -> SfuProjectionMutationResult[SfuFanoutRoute] | None:
        if candidate.status in _TERMINAL_STATUSES:
            return None
        audience = next(
            (
                value
                for value in self._store.audiences.values()
                if value.id == candidate.audience_projection_id
                and value.tenant_id == candidate.tenant_id
                and value.session_id == candidate.session_id
            ),
            None,
        )
        group = next(
            (
                value
                for value in self._store.groups.values()
                if value.id == candidate.receiver_group_projection_id
                and value.tenant_id == candidate.tenant_id
                and value.session_id == candidate.session_id
            ),
            None,
        )
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


def _prepare_mutation(
    mutation: SfuProjectionMutation[ProjectionT],
    *,
    current: ProjectionT | None,
    now: float,
    epoch_attributes: tuple[str, ...],
) -> tuple[
    SfuProjectionMutationResult[ProjectionT] | None,
    ProjectionT | None,
]:
    desired = mutation.value
    idempotency_digest = (
        _digest(mutation.idempotency_key)
        if mutation.idempotency_key is not None
        else desired.idempotency_key_digest
    )
    if current is not None and mutation.idempotency_key is not None:
        if current.idempotency_key_digest == idempotency_digest:
            if current.request_digest == desired.request_digest:
                return _result("saved", value=current, replayed=True), None
            return _result(
                "conflict",
                value=current,
                reason="idempotency_conflict",
            ), None
    if desired.status in _LIVE_STATUSES and desired.expires_at <= now:
        return _result("expired", value=current, reason="projection_expired"), None
    if current is None:
        if mutation.expected_version not in (None, 0):
            return _result("not_found", reason="projection_not_found"), None
        saved = replace(
            desired,
            version=1,
            idempotency_key_digest=idempotency_digest,
            updated_at=now,
            audited_at=now,
        )
        return None, saved
    if mutation.expected_version is None:
        return _result(
            "conflict",
            value=current,
            reason="projection_expected_version_required",
        ), None
    if mutation.expected_version != current.version:
        return _result(
            "conflict",
            value=current,
            reason="projection_version_conflict",
        ), None
    if current.status in _LIVE_STATUSES and current.expires_at <= now and desired.status in _LIVE_STATUSES:
        return _result("expired", value=current, reason="projection_expired"), None
    monotone_attributes = ("room_state_revision", "fencing_token", *epoch_attributes)
    if any(getattr(desired, name) < getattr(current, name) for name in monotone_attributes):
        return _result(
            "stale_epoch",
            value=current,
            reason="projection_epoch_stale",
        ), None
    saved = replace(
        desired,
        version=current.version + 1,
        idempotency_key_digest=idempotency_digest,
        created_at=current.created_at,
        updated_at=now,
        audited_at=now,
    )
    return None, saved


def _validate_mutation(mutation: SfuProjectionMutation[ProjectionT]) -> None:
    _validate_mutation_fence(mutation.expected_version, mutation.idempotency_key)
    _validate_projection(mutation.value)


def _validate_mutation_fence(
    expected_version: int | None,
    idempotency_key: str | None,
) -> None:
    if expected_version is None and idempotency_key is None:
        raise SfuBroadcastRepositoryError("projection_mutation_fence_required")
    if expected_version is not None and expected_version < 0:
        raise SfuBroadcastRepositoryError("projection_expected_version_invalid")
    if idempotency_key is not None and not 1 <= len(idempotency_key) <= 512:
        raise SfuBroadcastRepositoryError("projection_idempotency_key_invalid")


def _validate_projection(value: SfuProjectionEnvelope) -> None:
    _validate_scope(value.scope)
    for attribute in ("id", "room_state_id", "audit_actor_ref", "audit_reason"):
        _validate_identifier(getattr(value, attribute), attribute)
    if value.room_state_revision < 1 or value.version < 1 or value.fencing_token < 0:
        raise SfuBroadcastRepositoryError("projection_version_or_fence_invalid")
    if value.status not in _STATUSES or value.retention_status not in _RETENTION_STATUSES:
        raise SfuBroadcastRepositoryError("projection_lifecycle_status_invalid")
    if value.ttl_seconds < 1 or value.retention_seconds < 0:
        raise SfuBroadcastRepositoryError("projection_retention_invalid")
    if value.expires_at <= value.created_at or value.retain_until < value.expires_at:
        raise SfuBroadcastRepositoryError("projection_lifecycle_order_invalid")
    if value.updated_at < value.created_at or value.audited_at < value.created_at:
        raise SfuBroadcastRepositoryError("projection_audit_order_invalid")
    if (value.status == "tombstoned") != (value.tombstoned_at is not None):
        raise SfuBroadcastRepositoryError("projection_tombstone_state_invalid")
    if value.tombstoned_at is None and value.tombstone_reason is not None:
        raise SfuBroadcastRepositoryError("projection_tombstone_reason_invalid")
    for field in fields(value):
        if field.name.endswith("_digest") and len(getattr(value, field.name)) != 64:
            raise SfuBroadcastRepositoryError("projection_digest_invalid")
        if field.name.endswith("_epoch") and getattr(value, field.name) < 0:
            raise SfuBroadcastRepositoryError("projection_epoch_invalid")


def _filter_page(
    values: list[ProjectionT],
    *,
    mode: str,
    threshold: float | int | None,
) -> list[ProjectionT]:
    if mode == "all":
        return values
    if mode == "expired":
        return [
            value
            for value in values
            if value.status in _LIVE_STATUSES and value.expires_at <= cast(float, threshold)
        ]
    if mode == "reconciliation":
        return [
            value
            for value in values
            if value.room_state_revision < cast(int, threshold)
        ]
    if mode == "retention":
        return [
            value
            for value in values
            if value.retain_until <= cast(float, threshold)
            and value.retention_status != "purged"
        ]
    raise SfuBroadcastRepositoryError("projection_page_mode_invalid")


def _page_key(
    value: SfuProjectionEnvelope,
    mode: str,
    sort_attribute: str,
) -> tuple[str | float | int, str]:
    if mode == "expired":
        return value.expires_at, value.id
    if mode == "reconciliation":
        return value.room_state_revision, value.id
    if mode == "retention":
        return value.retain_until, value.id
    return getattr(value, sort_attribute), value.id


def _encode_cursor(mode: str, key: tuple[str | float | int, str]) -> str:
    payload = json.dumps([mode, key[0], key[1]], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str, mode: str) -> tuple[str | float | int, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        decoded = json.loads(raw)
        if (
            not isinstance(decoded, list)
            or len(decoded) != 3
            or decoded[0] != mode
            or not isinstance(decoded[1], (str, int, float))
            or not isinstance(decoded[2], str)
        ):
            raise ValueError
        return decoded[1], decoded[2]
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SfuBroadcastRepositoryError("projection_cursor_invalid") from error


def _result(
    status,
    *,
    value: ProjectionT | None = None,
    replayed: bool = False,
    reason: str | None = None,
) -> SfuProjectionMutationResult[ProjectionT]:
    return SfuProjectionMutationResult(status, value, replayed, reason)


def _sql_error_result(error: SQLAlchemyError) -> SfuProjectionMutationResult:
    message = str(error).lower()
    if "expired" in message:
        return _result("expired", reason="projection_expired")
    if "stale" in message or "non_monotone" in message:
        return _result("stale_epoch", reason="projection_epoch_stale")
    if "foreign key" in message or "orphan" in message:
        return _result("not_found", reason="projection_reference_not_found")
    return _result("conflict", reason="projection_write_conflict")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _expiry_request_digest(value: SfuProjectionEnvelope, now: float) -> str:
    return _digest(f"expire\0{value.tenant_id}\0{value.session_id}\0{value.id}\0{value.version}\0{now}")


def _key(scope: SfuBroadcastRoomScope, projection_id: str) -> tuple[str, str, str]:
    return scope.tenant_id, scope.session_id, projection_id


def _effective_now(now: float | None, clock: Callable[[], float]) -> float:
    value = float(clock() if now is None else now)
    if value < 0:
        raise SfuBroadcastRepositoryError("projection_now_invalid")
    return value


def _validate_scope(scope: SfuBroadcastRoomScope) -> None:
    _validate_identifier(scope.tenant_id, "tenant_id")
    _validate_identifier(scope.session_id, "session_id")


def _validate_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 255:
        raise SfuBroadcastRepositoryError(f"{label}_invalid")


def _validate_page_size(page_size: int, page_size_max: int) -> None:
    if not 1 <= page_size <= page_size_max:
        raise SfuBroadcastRepositoryError("projection_page_size_invalid")


def _validate_page_size_max(page_size_max: int) -> None:
    if not 1 <= page_size_max <= 10_000:
        raise SfuBroadcastRepositoryError("projection_page_size_max_invalid")


__all__ = [
    "InMemorySfuBroadcastAudienceRepository",
    "InMemorySfuBroadcastRepositoryStore",
    "InMemorySfuFanoutRouteRepository",
    "InMemorySfuReceiverGroupRepository",
    "SfuBroadcastRepositoryError",
    "SqlSfuBroadcastAudienceRepository",
    "SqlSfuFanoutRouteRepository",
    "SqlSfuReceiverGroupRepository",
]
