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
from agent.db_models.sfu_broadcast_retention import (
    SfuAudienceRetentionFenceDB,
    SfuAudienceSnapshotTombstoneDB,
)
from agent.services.sfu_broadcast_repository_ports import (
    SfuAudienceRetentionFence,
    SfuAudienceRetentionPurgePage,
    SfuAtomicGroupProjectionMutation,
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
        self.audience_tombstones: dict[str, tuple[int, str, int, float, float]] = {}
        self.retention_owner_id: str | None = None
        self.retention_fencing_token: int = 0
        self.retention_lease_expires_at: float = 0.0


class InMemorySfuAtomicGroupProjectionRepository:
    """Atomic Hub-side Audience epoch check plus receiver-group CAS."""

    def __init__(
        self,
        *,
        store: InMemorySfuBroadcastRepositoryStore,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
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
        key = _key(desired.scope, desired.id)
        with self._store.lock:
            current = self._store.groups.get(key)
            outcome, saved = _prepare_mutation(
                mutation.mutation,
                current=current,
                now=effective_now,
                epoch_attributes=("membership_epoch", "key_epoch", "topology_epoch"),
            )
            if outcome is not None:
                return outcome
            assert saved is not None
            parent = self._store.audiences.get(
                _key(saved.scope, mutation.audience_projection_id)
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
            if any(
                existing.id != saved.id
                and existing.scope == saved.scope
                and existing.status == "active"
                and existing.tombstoned_at is None
                and existing.subscription_ref == saved.subscription_ref
                for existing in self._store.groups.values()
            ):
                return _result("conflict", reason="active_projection_conflict")
            self._store.groups[key] = saved
            return _result("saved", value=saved)


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
            if (
                isinstance(mutation.value, SfuBroadcastAudience)
                and _audience_tombstone_id(scope, mutation.value.id)
                in self._store.audience_tombstones
            ):
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


class InMemorySfuAudienceSnapshotRetentionRepository:
    """Atomic content erasure over a shared projection store."""

    def __init__(
        self,
        *,
        store: InMemorySfuBroadcastRepositoryStore,
        tombstone_retention_seconds: int = 30 * 86_400,
    ) -> None:
        self._store = store
        self._tombstone_retention_seconds = tombstone_retention_seconds

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
        with self._store.lock:
            self._claim_fence(fence, now)
            key = _key(scope, projection_id)
            current = self._store.audiences.get(key)
            if current is None:
                tombstone = self._store.audience_tombstones.get(_audience_tombstone_id(scope, projection_id))
                return _result(
                    "saved" if tombstone else "not_found",
                    replayed=tombstone is not None,
                    reason=None if tombstone else "projection_not_found",
                )
            if current.status == "tombstoned":
                return _result("saved", value=current, replayed=True)
            if current.version != expected_version:
                return _result("conflict", value=current, reason="projection_version_conflict")
            if purge_deadline < now or purge_deadline < current.expires_at:
                return _result("conflict", value=current, reason="retention_deadline_invalid")
            if self._live_route_exists(current.id, now):
                return _result("conflict", value=current, reason="audience_snapshot_route_active")
            saved = replace(
                current,
                status="tombstoned",
                retention_status="purge_pending",
                retain_until=purge_deadline,
                tombstoned_at=now,
                tombstone_reason=retention_reason,
                fencing_token=fence.fencing_token,
                version=current.version + 1,
                request_digest=hashlib.sha256(
                    f"retention\0{current.request_digest}\0{retention_reason}\0{expected_version}".encode()
                ).hexdigest(),
                idempotency_key_digest=hashlib.sha256(
                    f"retention\0{projection_id}\0{retention_reason}".encode()
                ).hexdigest(),
                updated_at=now,
                audited_at=now,
            )
            self._store.audiences[key] = saved
            return _result("saved", value=saved)

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
        purged = 0
        with self._store.lock:
            self._claim_fence(fence, now)
            rows = sorted(
                (
                    value for value in self._store.audiences.values()
                    if value.status == "tombstoned" and value.retention_status == "purge_pending"
                    and value.retain_until <= now
                    and (after is None or (value.retain_until, value.id) > after)
                ),
                key=lambda value: (value.retain_until, value.id),
            )[: page_size + 1]
            selected = rows[:page_size]
            for audience in selected:
                if self._live_route_exists(audience.id, now):
                    continue
                group_ids: set[str] = set()
                for route_key, route in tuple(self._store.routes.items()):
                    if route.audience_projection_id != audience.id:
                        continue
                    if route.status not in _TERMINAL_STATUSES:
                        continue
                    group_ids.add(route.receiver_group_projection_id)
                    self._store.routes.pop(route_key, None)
                for group_id in group_ids:
                    still_referenced = any(
                        route.receiver_group_projection_id == group_id
                        for route in self._store.routes.values()
                    )
                    if not still_referenced:
                        self._store.groups.pop(_key(audience.scope, group_id), None)
                self._store.audiences.pop(_key(audience.scope, audience.id), None)
                self._store.audience_tombstones[_audience_tombstone_id(audience.scope, audience.id)] = (
                    audience.version,
                    audience.tombstone_reason or "retention_expired",
                    fence.fencing_token,
                    now,
                    now + self._tombstone_retention_seconds,
                )
                purged += 1
            next_cursor = (
                _encode_retention_cursor((selected[-1].retain_until, selected[-1].id))
                if len(rows) > page_size and selected else None
            )
            return SfuAudienceRetentionPurgePage(purged, next_cursor)

    def _live_route_exists(self, audience_id: str, now: float) -> bool:
        return any(
            route.audience_projection_id == audience_id
            and route.status in _LIVE_STATUSES
            and route.expires_at > now
            for route in self._store.routes.values()
        )

    def _claim_fence(self, fence: SfuAudienceRetentionFence, now: float) -> None:
        if fence.fencing_token < self._store.retention_fencing_token:
            raise SfuBroadcastRepositoryError("audience_retention_fence_stale")
        if (
            fence.fencing_token == self._store.retention_fencing_token
            and self._store.retention_owner_id not in {None, fence.owner_id}
            and self._store.retention_lease_expires_at > now
        ):
            raise SfuBroadcastRepositoryError("audience_retention_lease_conflict")
        self._store.retention_owner_id = fence.owner_id
        self._store.retention_fencing_token = fence.fencing_token
        self._store.retention_lease_expires_at = fence.lease_expires_at


class SqlSfuAudienceSnapshotRetentionRepository:
    def __init__(
        self,
        *,
        db_engine=default_engine,
        tombstone_retention_seconds: int = 30 * 86_400,
    ) -> None:
        self._engine = db_engine
        self._tombstone_retention_seconds = tombstone_retention_seconds

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
                saved = SqlSfuBroadcastAudienceRepository(db_engine=self._engine).get(scope, projection_id)
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


def _validate_atomic_group_mutation(
    mutation: SfuAtomicGroupProjectionMutation,
) -> None:
    if not isinstance(mutation, SfuAtomicGroupProjectionMutation):
        raise SfuBroadcastRepositoryError("group_projection_mutation_invalid")
    _validate_identifier(
        mutation.audience_projection_id, "audience_projection_id"
    )
    _validate_mutation(mutation.mutation)
    epochs = (
        mutation.expected_policy_epoch,
        mutation.expected_membership_epoch,
        mutation.expected_key_epoch,
    )
    if any(type(epoch) is not int or epoch < 0 for epoch in epochs):
        raise SfuBroadcastRepositoryError("group_projection_epoch_invalid")


def _validate_atomic_group_parent(
    mutation: SfuAtomicGroupProjectionMutation,
    *,
    parent: SfuBroadcastAudience | None,
    current: SfuReceiverGroup | None,
    saved: SfuReceiverGroup,
    now: float,
) -> SfuProjectionMutationResult[SfuReceiverGroup] | None:
    if parent is None:
        return _result("not_found", reason="group_projection_parent_not_found")
    if (
        parent.status != "active"
        or parent.expires_at <= now
        or parent.room_state_revision != saved.room_state_revision
        or saved.expires_at > parent.expires_at
    ):
        return _result("stale_epoch", reason="group_projection_parent_stale")
    if (
        parent.policy_epoch != mutation.expected_policy_epoch
        or parent.membership_epoch != mutation.expected_membership_epoch
        or parent.key_epoch != mutation.expected_key_epoch
        or saved.membership_epoch != mutation.expected_membership_epoch
        or saved.key_epoch != mutation.expected_key_epoch
    ):
        return _result("stale_epoch", reason="group_projection_epoch_stale")
    if saved.fencing_token <= 0 or (
        current is not None and saved.fencing_token <= current.fencing_token
    ):
        return _result("stale_epoch", reason="group_projection_fencing_stale")
    return None


def _audience_from_row(row: SfuBroadcastAudienceDB) -> SfuBroadcastAudience:
    return SfuBroadcastAudience(
        **{field.name: getattr(row, field.name) for field in fields(SfuBroadcastAudience)}
    )


def _receiver_group_from_row(row: SfuReceiverGroupDB) -> SfuReceiverGroup:
    return SfuReceiverGroup(
        **{field.name: getattr(row, field.name) for field in fields(SfuReceiverGroup)}
    )


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


def _validate_retention_fence(fence: SfuAudienceRetentionFence, now: float) -> None:
    if (
        not fence.owner_id or type(fence.fencing_token) is not int
        or fence.fencing_token < 1 or fence.lease_expires_at <= now
    ):
        raise SfuBroadcastRepositoryError("audience_retention_fence_invalid")


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


def _audience_tombstone_id(scope: SfuBroadcastRoomScope, projection_id: str) -> str:
    raw = f"ananta:sfu-audience-tombstone:v1\0{scope.tenant_id}\0{scope.session_id}\0{projection_id}"
    return "sfu-audience-tombstone-" + hashlib.sha256(raw.encode()).hexdigest()


def _scope_digest(tenant_id: str, session_id: str) -> str:
    return hashlib.sha256(f"ananta:sfu-audience-scope:v1\0{tenant_id}\0{session_id}".encode()).hexdigest()


def _encode_retention_cursor(key: tuple[float, str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(key, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_retention_cursor(cursor: str) -> tuple[float, str]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(raw, list) or len(raw) != 2 or not isinstance(raw[0], (int, float)) or not isinstance(raw[1], str):
            raise ValueError
        return float(raw[0]), raw[1]
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise SfuBroadcastRepositoryError("audience_retention_cursor_invalid") from exc


__all__ = [
    "InMemorySfuAudienceSnapshotRetentionRepository",
    "InMemorySfuAtomicGroupProjectionRepository",
    "InMemorySfuBroadcastAudienceRepository",
    "InMemorySfuBroadcastRepositoryStore",
    "InMemorySfuFanoutRouteRepository",
    "InMemorySfuReceiverGroupRepository",
    "SfuBroadcastRepositoryError",
    "SqlSfuBroadcastAudienceRepository",
    "SqlSfuAudienceSnapshotRetentionRepository",
    "SqlSfuAtomicGroupProjectionRepository",
    "SqlSfuFanoutRouteRepository",
    "SqlSfuReceiverGroupRepository",
]
