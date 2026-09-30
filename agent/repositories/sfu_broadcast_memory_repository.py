"""In-memory SFU broadcast projection adapters over a shareable restart-equivalent store."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import replace
from typing import Callable, Generic

from agent.models.sfu_broadcast_projection import (
    SfuAtomicGroupProjectionMutation,
    SfuAudienceRetentionFence,
    SfuAudienceRetentionPurgePage,
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
    _decode_retention_cursor,
    _encode_cursor,
    _encode_retention_cursor,
    _filter_page,
    _page_key,
)
from agent.repositories.sfu_broadcast_projection_rules import (
    _LIVE_STATUSES,
    _TERMINAL_STATUSES,
    ProjectionT,
    SfuBroadcastRepositoryError,
    _audience_tombstone_id,
    _effective_now,
    _expiry_request_digest,
    _key,
    _prepare_mutation,
    _result,
    _validate_atomic_group_mutation,
    _validate_atomic_group_parent,
    _validate_identifier,
    _validate_mutation,
    _validate_mutation_fence,
    _validate_page_size,
    _validate_page_size_max,
    _validate_retention_fence,
    _validate_scope,
)


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
