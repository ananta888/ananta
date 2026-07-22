"""Versioned persistence boundary for Hub-owned SFU broadcast rollout flags."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Mapping, Protocol

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import SfuBroadcastFeatureFlagDB, SfuBroadcastFeatureFlagMutationDB


SECURITY_LATCH_FLAGS = frozenset(
    {
        "immediate_security_fence",
        "semantic_media_immediate_security_fence",
        "stop_admission",
        "semantic_media_stop_admission",
    }
)
_FLAG_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


class SfuBroadcastFeatureFlagRepositoryError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuBroadcastFeatureFlagScope:
    tenant_id: str
    region: str = "*"
    room_cohort: str = "*"


@dataclass(frozen=True, slots=True)
class SfuBroadcastFeatureFlagMutation:
    scope: SfuBroadcastFeatureFlagScope
    flag: str
    enabled: bool
    rollout_stage: str
    actor: str
    reason: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class SfuBroadcastFeatureFlagState:
    scope: SfuBroadcastFeatureFlagScope
    flag: str
    enabled: bool
    rollout_stage: str
    version: int
    actor: str
    reason: str
    idempotency_key_digest: str
    audited_at: float


@dataclass(frozen=True, slots=True)
class SfuBroadcastFeatureFlagMutationResult:
    status: str
    state: SfuBroadcastFeatureFlagState | None = None
    reason_code: str | None = None

    @property
    def committed(self) -> bool:
        return self.status in {"created", "updated", "replayed"}


@dataclass(frozen=True, slots=True)
class SfuBroadcastFeatureFlagSnapshot:
    scope: SfuBroadcastFeatureFlagScope
    flags: Mapping[str, SfuBroadcastFeatureFlagState]
    available: bool = True

    def enabled(self, flag: str) -> bool:
        """Return false for absent flags and for unavailable persistence."""

        if not self.available:
            return False
        state = self.flags.get(flag)
        return bool(state.enabled) if state is not None else False


@dataclass(frozen=True, slots=True)
class SfuBroadcastFeatureFlagPage:
    items: tuple[SfuBroadcastFeatureFlagState, ...]
    next_cursor: str | None
    available: bool = True


class SfuBroadcastFeatureFlagRepositoryPort(Protocol):
    def create(
        self,
        mutation: SfuBroadcastFeatureFlagMutation,
        *,
        expected_version: int,
    ) -> SfuBroadcastFeatureFlagMutationResult: ...

    def compare_and_swap(
        self,
        mutation: SfuBroadcastFeatureFlagMutation,
        *,
        expected_version: int,
    ) -> SfuBroadcastFeatureFlagMutationResult: ...

    def snapshot(self, scope: SfuBroadcastFeatureFlagScope) -> SfuBroadcastFeatureFlagSnapshot: ...

    def page(
        self,
        tenant_id: str,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> SfuBroadcastFeatureFlagPage: ...


@dataclass(frozen=True, slots=True)
class _MutationReceipt:
    request_digest: str
    state: SfuBroadcastFeatureFlagState


class InMemorySfuBroadcastFeatureFlagStore:
    """Shareable test store so fresh adapter instances model Hub restarts."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.states: dict[tuple[str, str, str, str], SfuBroadcastFeatureFlagState] = {}
        self.receipts: dict[tuple[str, str], _MutationReceipt] = {}
        self.available = True


class InMemorySfuBroadcastFeatureFlagRepository:
    def __init__(
        self,
        *,
        store: InMemorySfuBroadcastFeatureFlagStore | None = None,
        clock=time.time,
    ) -> None:
        self._store = store or InMemorySfuBroadcastFeatureFlagStore()
        self._clock = clock

    def set_available(self, available: bool) -> None:
        with self._store.lock:
            self._store.available = available

    def create(
        self,
        mutation: SfuBroadcastFeatureFlagMutation,
        *,
        expected_version: int,
    ) -> SfuBroadcastFeatureFlagMutationResult:
        if expected_version != 0:
            raise SfuBroadcastFeatureFlagRepositoryError("feature_flag_create_expected_version_invalid")
        return self._mutate(mutation, expected_version=expected_version, create_only=True)

    def compare_and_swap(
        self,
        mutation: SfuBroadcastFeatureFlagMutation,
        *,
        expected_version: int,
    ) -> SfuBroadcastFeatureFlagMutationResult:
        if expected_version < 1:
            raise SfuBroadcastFeatureFlagRepositoryError("feature_flag_cas_expected_version_invalid")
        return self._mutate(mutation, expected_version=expected_version, create_only=False)

    def snapshot(self, scope: SfuBroadcastFeatureFlagScope) -> SfuBroadcastFeatureFlagSnapshot:
        _validate_scope(scope)
        with self._store.lock:
            if not self._store.available:
                return SfuBroadcastFeatureFlagSnapshot(scope, {}, available=False)
            flags = {
                state.flag: state
                for state in self._store.states.values()
                if state.scope == scope
            }
            return SfuBroadcastFeatureFlagSnapshot(scope, flags)

    def page(
        self,
        tenant_id: str,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> SfuBroadcastFeatureFlagPage:
        _validate_page(tenant_id, limit)
        after = _decode_cursor(cursor) if cursor else None
        with self._store.lock:
            if not self._store.available:
                return SfuBroadcastFeatureFlagPage((), None, available=False)
            ordered = sorted(
                (state for state in self._store.states.values() if state.scope.tenant_id == tenant_id),
                key=_state_order_key,
            )
            if after is not None:
                ordered = [state for state in ordered if _state_order_key(state) > after]
            selected = ordered[: limit + 1]
            has_more = len(selected) > limit
            items = tuple(selected[:limit])
            next_cursor = _encode_cursor(_state_order_key(items[-1])) if has_more and items else None
            return SfuBroadcastFeatureFlagPage(items, next_cursor)

    def _mutate(
        self,
        mutation: SfuBroadcastFeatureFlagMutation,
        *,
        expected_version: int,
        create_only: bool,
    ) -> SfuBroadcastFeatureFlagMutationResult:
        _validate_mutation(mutation, expected_version)
        request_digest = _request_digest(mutation, expected_version)
        idempotency_digest = _digest(mutation.idempotency_key)
        receipt_key = (mutation.scope.tenant_id, idempotency_digest)
        state_key = _state_key(mutation.scope, mutation.flag)
        with self._store.lock:
            if not self._store.available:
                return SfuBroadcastFeatureFlagMutationResult(
                    "unavailable", reason_code="feature_flag_store_unavailable"
                )
            receipt = self._store.receipts.get(receipt_key)
            if receipt is not None:
                return _replay(receipt.request_digest, request_digest, receipt.state)
            current = self._store.states.get(state_key)
            if create_only:
                if current is not None:
                    return _conflict(current, "feature_flag_already_exists")
            elif current is None or current.version != expected_version:
                return _conflict(current, "feature_flag_version_conflict")
            if current is not None and _security_latch_set(current, mutation):
                return _conflict(current, "feature_flag_security_latch_monotone")
            now = float(self._clock())
            state = _next_state(mutation, expected_version, idempotency_digest, now)
            self._store.states[state_key] = state
            self._store.receipts[receipt_key] = _MutationReceipt(request_digest, state)
            return SfuBroadcastFeatureFlagMutationResult("created" if create_only else "updated", state)


class SqlSfuBroadcastFeatureFlagRepository:
    """SQL adapter with atomic optimistic fencing and durable replay receipts."""

    def __init__(self, *, db_engine=default_engine, clock=time.time) -> None:
        self._engine = db_engine
        self._clock = clock

    def create(
        self,
        mutation: SfuBroadcastFeatureFlagMutation,
        *,
        expected_version: int,
    ) -> SfuBroadcastFeatureFlagMutationResult:
        if expected_version != 0:
            raise SfuBroadcastFeatureFlagRepositoryError("feature_flag_create_expected_version_invalid")
        return self._mutate(mutation, expected_version=expected_version, create_only=True)

    def compare_and_swap(
        self,
        mutation: SfuBroadcastFeatureFlagMutation,
        *,
        expected_version: int,
    ) -> SfuBroadcastFeatureFlagMutationResult:
        if expected_version < 1:
            raise SfuBroadcastFeatureFlagRepositoryError("feature_flag_cas_expected_version_invalid")
        return self._mutate(mutation, expected_version=expected_version, create_only=False)

    def snapshot(self, scope: SfuBroadcastFeatureFlagScope) -> SfuBroadcastFeatureFlagSnapshot:
        _validate_scope(scope)
        try:
            with Session(self._engine) as db:
                rows = db.exec(
                    select(SfuBroadcastFeatureFlagDB)
                    .where(
                        SfuBroadcastFeatureFlagDB.tenant_id == scope.tenant_id,
                        SfuBroadcastFeatureFlagDB.region == scope.region,
                        SfuBroadcastFeatureFlagDB.room_cohort == scope.room_cohort,
                    )
                    .order_by(SfuBroadcastFeatureFlagDB.flag)
                ).all()
                states = [_state_from_row(row) for row in rows]
                return SfuBroadcastFeatureFlagSnapshot(scope, {state.flag: state for state in states})
        except SQLAlchemyError:
            return SfuBroadcastFeatureFlagSnapshot(scope, {}, available=False)

    def page(
        self,
        tenant_id: str,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> SfuBroadcastFeatureFlagPage:
        _validate_page(tenant_id, limit)
        after = _decode_cursor(cursor) if cursor else None
        try:
            with Session(self._engine) as db:
                statement = select(SfuBroadcastFeatureFlagDB).where(
                    SfuBroadcastFeatureFlagDB.tenant_id == tenant_id
                )
                if after is not None:
                    region, cohort, flag, row_id = after
                    statement = statement.where(
                        sa.or_(
                            SfuBroadcastFeatureFlagDB.region > region,
                            sa.and_(
                                SfuBroadcastFeatureFlagDB.region == region,
                                SfuBroadcastFeatureFlagDB.room_cohort > cohort,
                            ),
                            sa.and_(
                                SfuBroadcastFeatureFlagDB.region == region,
                                SfuBroadcastFeatureFlagDB.room_cohort == cohort,
                                SfuBroadcastFeatureFlagDB.flag > flag,
                            ),
                            sa.and_(
                                SfuBroadcastFeatureFlagDB.region == region,
                                SfuBroadcastFeatureFlagDB.room_cohort == cohort,
                                SfuBroadcastFeatureFlagDB.flag == flag,
                                SfuBroadcastFeatureFlagDB.id > row_id,
                            ),
                        )
                    )
                rows = db.exec(
                    statement.order_by(
                        SfuBroadcastFeatureFlagDB.region,
                        SfuBroadcastFeatureFlagDB.room_cohort,
                        SfuBroadcastFeatureFlagDB.flag,
                        SfuBroadcastFeatureFlagDB.id,
                    ).limit(limit + 1)
                ).all()
                has_more = len(rows) > limit
                included = rows[:limit]
                items = tuple(_state_from_row(row) for row in included)
                next_cursor = _encode_cursor(_row_order_key(included[-1])) if has_more and included else None
                return SfuBroadcastFeatureFlagPage(items, next_cursor)
        except SQLAlchemyError:
            return SfuBroadcastFeatureFlagPage((), None, available=False)

    def _mutate(
        self,
        mutation: SfuBroadcastFeatureFlagMutation,
        *,
        expected_version: int,
        create_only: bool,
    ) -> SfuBroadcastFeatureFlagMutationResult:
        _validate_mutation(mutation, expected_version)
        request_digest = _request_digest(mutation, expected_version)
        idempotency_digest = _digest(mutation.idempotency_key)
        state_id = _flag_id(mutation.scope, mutation.flag)
        raced = False
        try:
            with Session(self._engine) as db:
                receipt = db.exec(
                    select(SfuBroadcastFeatureFlagMutationDB).where(
                        SfuBroadcastFeatureFlagMutationDB.tenant_id == mutation.scope.tenant_id,
                        SfuBroadcastFeatureFlagMutationDB.idempotency_key_digest == idempotency_digest,
                    )
                ).first()
                if receipt is not None:
                    return _replay(receipt.request_digest, request_digest, _state_from_receipt(receipt))
                current = db.get(SfuBroadcastFeatureFlagDB, state_id)
                if create_only:
                    if current is not None:
                        return _conflict(_state_from_row(current), "feature_flag_already_exists")
                elif current is None or current.version != expected_version:
                    return _conflict(
                        _state_from_row(current) if current is not None else None,
                        "feature_flag_version_conflict",
                    )
                if current is not None and _security_latch_set(_state_from_row(current), mutation):
                    return _conflict(
                        _state_from_row(current),
                        "feature_flag_security_latch_monotone",
                    )
                now = float(self._clock())
                state = _next_state(mutation, expected_version, idempotency_digest, now)
                result_status = "created" if create_only else "updated"
                if create_only:
                    db.add(_row_from_state(state_id, state, created_at=now))
                else:
                    updated = db.exec(
                        sa.update(SfuBroadcastFeatureFlagDB)
                        .where(
                            SfuBroadcastFeatureFlagDB.id == state_id,
                            SfuBroadcastFeatureFlagDB.tenant_id == mutation.scope.tenant_id,
                            SfuBroadcastFeatureFlagDB.version == expected_version,
                        )
                        .values(
                            enabled=state.enabled,
                            rollout_stage=state.rollout_stage,
                            version=state.version,
                            actor=state.actor,
                            reason=state.reason,
                            idempotency_key_digest=state.idempotency_key_digest,
                            audited_at=state.audited_at,
                            updated_at=now,
                        )
                    )
                    if int(getattr(updated, "rowcount", 0) or 0) != 1:
                        db.rollback()
                        return SfuBroadcastFeatureFlagMutationResult(
                            "conflict", reason_code="feature_flag_version_conflict"
                        )
                db.add(
                    _receipt_row(
                        state_id,
                        mutation,
                        expected_version,
                        state,
                        result_status,
                        idempotency_digest,
                        request_digest,
                    )
                )
                try:
                    db.commit()
                    return SfuBroadcastFeatureFlagMutationResult(result_status, state)
                except IntegrityError:
                    db.rollback()
                    raced = True
        except SQLAlchemyError:
            return SfuBroadcastFeatureFlagMutationResult(
                "unavailable", reason_code="feature_flag_store_unavailable"
            )
        if raced:
            return self._result_after_race(mutation.scope.tenant_id, idempotency_digest, request_digest)
        return SfuBroadcastFeatureFlagMutationResult(
            "conflict", reason_code="feature_flag_version_conflict"
        )

    def _result_after_race(
        self,
        tenant_id: str,
        idempotency_digest: str,
        request_digest: str,
    ) -> SfuBroadcastFeatureFlagMutationResult:
        try:
            with Session(self._engine) as db:
                receipt = db.exec(
                    select(SfuBroadcastFeatureFlagMutationDB).where(
                        SfuBroadcastFeatureFlagMutationDB.tenant_id == tenant_id,
                        SfuBroadcastFeatureFlagMutationDB.idempotency_key_digest == idempotency_digest,
                    )
                ).first()
                if receipt is None:
                    return SfuBroadcastFeatureFlagMutationResult(
                        "conflict", reason_code="feature_flag_version_conflict"
                    )
                return _replay(receipt.request_digest, request_digest, _state_from_receipt(receipt))
        except SQLAlchemyError:
            return SfuBroadcastFeatureFlagMutationResult(
                "unavailable", reason_code="feature_flag_store_unavailable"
            )


def _validate_scope(scope: SfuBroadcastFeatureFlagScope) -> None:
    _bounded(scope.tenant_id, "feature_flag_tenant_invalid", 255)
    _bounded(scope.region, "feature_flag_region_invalid", 128)
    _bounded(scope.room_cohort, "feature_flag_room_cohort_invalid", 255)


def _validate_mutation(mutation: SfuBroadcastFeatureFlagMutation, expected_version: int) -> None:
    _validate_scope(mutation.scope)
    if isinstance(expected_version, bool) or expected_version < 0:
        raise SfuBroadcastFeatureFlagRepositoryError("feature_flag_expected_version_invalid")
    if not _FLAG_PATTERN.fullmatch(mutation.flag):
        raise SfuBroadcastFeatureFlagRepositoryError("feature_flag_name_invalid")
    if type(mutation.enabled) is not bool:
        raise SfuBroadcastFeatureFlagRepositoryError("feature_flag_value_invalid")
    _bounded(mutation.rollout_stage, "feature_flag_rollout_stage_invalid", 64)
    _bounded(mutation.actor, "feature_flag_actor_invalid", 255)
    _bounded(mutation.reason, "feature_flag_reason_invalid", 1_024)
    _bounded(mutation.idempotency_key, "feature_flag_idempotency_key_invalid", 255)


def _validate_page(tenant_id: str, limit: int) -> None:
    _bounded(tenant_id, "feature_flag_tenant_invalid", 255)
    if isinstance(limit, bool) or not 1 <= limit <= 500:
        raise SfuBroadcastFeatureFlagRepositoryError("feature_flag_page_limit_invalid")


def _bounded(value: str, reason_code: str, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SfuBroadcastFeatureFlagRepositoryError(reason_code)


def _security_latch_set(
    current: SfuBroadcastFeatureFlagState,
    mutation: SfuBroadcastFeatureFlagMutation,
) -> bool:
    return current.flag in SECURITY_LATCH_FLAGS and current.enabled and not mutation.enabled


def _next_state(
    mutation: SfuBroadcastFeatureFlagMutation,
    expected_version: int,
    idempotency_digest: str,
    audited_at: float,
) -> SfuBroadcastFeatureFlagState:
    return SfuBroadcastFeatureFlagState(
        scope=mutation.scope,
        flag=mutation.flag,
        enabled=mutation.enabled,
        rollout_stage=mutation.rollout_stage,
        version=expected_version + 1,
        actor=mutation.actor,
        reason=mutation.reason,
        idempotency_key_digest=idempotency_digest,
        audited_at=audited_at,
    )


def _request_digest(mutation: SfuBroadcastFeatureFlagMutation, expected_version: int) -> str:
    payload = {
        "actor": mutation.actor,
        "enabled": mutation.enabled,
        "expected_version": expected_version,
        "flag": mutation.flag,
        "reason": mutation.reason,
        "region": mutation.scope.region,
        "rollout_stage": mutation.rollout_stage,
        "room_cohort": mutation.scope.room_cohort,
        "tenant_id": mutation.scope.tenant_id,
    }
    return _digest(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _replay(
    stored_digest: str,
    request_digest: str,
    state: SfuBroadcastFeatureFlagState,
) -> SfuBroadcastFeatureFlagMutationResult:
    if stored_digest != request_digest:
        raise SfuBroadcastFeatureFlagRepositoryError("feature_flag_idempotency_conflict")
    return SfuBroadcastFeatureFlagMutationResult("replayed", state)


def _conflict(
    state: SfuBroadcastFeatureFlagState | None,
    reason_code: str,
) -> SfuBroadcastFeatureFlagMutationResult:
    return SfuBroadcastFeatureFlagMutationResult("conflict", state, reason_code)


def _state_key(scope: SfuBroadcastFeatureFlagScope, flag: str) -> tuple[str, str, str, str]:
    return scope.tenant_id, scope.region, scope.room_cohort, flag


def _flag_id(scope: SfuBroadcastFeatureFlagScope, flag: str) -> str:
    return _digest("\0".join((*_state_key(scope, flag),)))


def _mutation_id(tenant_id: str, idempotency_digest: str) -> str:
    return _digest(f"{tenant_id}\0{idempotency_digest}")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _row_from_state(
    row_id: str,
    state: SfuBroadcastFeatureFlagState,
    *,
    created_at: float,
) -> SfuBroadcastFeatureFlagDB:
    return SfuBroadcastFeatureFlagDB(
        id=row_id,
        tenant_id=state.scope.tenant_id,
        region=state.scope.region,
        room_cohort=state.scope.room_cohort,
        flag=state.flag,
        enabled=state.enabled,
        rollout_stage=state.rollout_stage,
        version=state.version,
        actor=state.actor,
        reason=state.reason,
        idempotency_key_digest=state.idempotency_key_digest,
        audited_at=state.audited_at,
        created_at=created_at,
        updated_at=created_at,
    )


def _receipt_row(
    state_id: str,
    mutation: SfuBroadcastFeatureFlagMutation,
    expected_version: int,
    state: SfuBroadcastFeatureFlagState,
    result_status: str,
    idempotency_digest: str,
    request_digest: str,
) -> SfuBroadcastFeatureFlagMutationDB:
    return SfuBroadcastFeatureFlagMutationDB(
        id=_mutation_id(mutation.scope.tenant_id, idempotency_digest),
        feature_flag_id=state_id,
        tenant_id=mutation.scope.tenant_id,
        region=mutation.scope.region,
        room_cohort=mutation.scope.room_cohort,
        flag=mutation.flag,
        enabled=mutation.enabled,
        rollout_stage=mutation.rollout_stage,
        expected_version=expected_version,
        result_version=state.version,
        result_status=result_status,
        actor=mutation.actor,
        reason=mutation.reason,
        idempotency_key_digest=idempotency_digest,
        request_digest=request_digest,
        audited_at=state.audited_at,
    )


def _state_from_row(row: SfuBroadcastFeatureFlagDB) -> SfuBroadcastFeatureFlagState:
    return SfuBroadcastFeatureFlagState(
        scope=SfuBroadcastFeatureFlagScope(row.tenant_id, row.region, row.room_cohort),
        flag=row.flag,
        enabled=row.enabled,
        rollout_stage=row.rollout_stage,
        version=row.version,
        actor=row.actor,
        reason=row.reason,
        idempotency_key_digest=row.idempotency_key_digest,
        audited_at=row.audited_at,
    )


def _state_from_receipt(row: SfuBroadcastFeatureFlagMutationDB) -> SfuBroadcastFeatureFlagState:
    return SfuBroadcastFeatureFlagState(
        scope=SfuBroadcastFeatureFlagScope(row.tenant_id, row.region, row.room_cohort),
        flag=row.flag,
        enabled=row.enabled,
        rollout_stage=row.rollout_stage,
        version=row.result_version,
        actor=row.actor,
        reason=row.reason,
        idempotency_key_digest=row.idempotency_key_digest,
        audited_at=row.audited_at,
    )


def _state_order_key(state: SfuBroadcastFeatureFlagState) -> tuple[str, str, str, str]:
    return state.scope.region, state.scope.room_cohort, state.flag, _flag_id(state.scope, state.flag)


def _row_order_key(row: SfuBroadcastFeatureFlagDB) -> tuple[str, str, str, str]:
    return row.region, row.room_cohort, row.flag, row.id


def _encode_cursor(key: tuple[str, str, str, str]) -> str:
    raw = json.dumps(key, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str, str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise SfuBroadcastFeatureFlagRepositoryError("feature_flag_cursor_invalid") from exc
    if not isinstance(value, list) or len(value) != 4 or not all(isinstance(item, str) for item in value):
        raise SfuBroadcastFeatureFlagRepositoryError("feature_flag_cursor_invalid")
    return value[0], value[1], value[2], value[3]


__all__ = [
    "InMemorySfuBroadcastFeatureFlagRepository",
    "InMemorySfuBroadcastFeatureFlagStore",
    "SECURITY_LATCH_FLAGS",
    "SfuBroadcastFeatureFlagMutation",
    "SfuBroadcastFeatureFlagMutationResult",
    "SfuBroadcastFeatureFlagPage",
    "SfuBroadcastFeatureFlagRepositoryError",
    "SfuBroadcastFeatureFlagRepositoryPort",
    "SfuBroadcastFeatureFlagScope",
    "SfuBroadcastFeatureFlagSnapshot",
    "SfuBroadcastFeatureFlagState",
    "SqlSfuBroadcastFeatureFlagRepository",
]
