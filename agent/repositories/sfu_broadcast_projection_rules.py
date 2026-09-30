"""Shared validation, mutation and identity rules for SFU broadcast projection adapters."""

from __future__ import annotations

import hashlib
from dataclasses import fields, replace
from typing import Callable, TypeVar

from sqlalchemy.exc import SQLAlchemyError

from agent.models.sfu_broadcast_projection import (
    SfuAtomicGroupProjectionMutation,
    SfuAudienceRetentionFence,
    SfuBroadcastAudience,
    SfuBroadcastRoomScope,
    SfuProjectionEnvelope,
    SfuProjectionMutation,
    SfuProjectionMutationResult,
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


def _audience_tombstone_id(scope: SfuBroadcastRoomScope, projection_id: str) -> str:
    raw = f"ananta:sfu-audience-tombstone:v1\0{scope.tenant_id}\0{scope.session_id}\0{projection_id}"
    return "sfu-audience-tombstone-" + hashlib.sha256(raw.encode()).hexdigest()


def _scope_digest(tenant_id: str, session_id: str) -> str:
    return hashlib.sha256(f"ananta:sfu-audience-scope:v1\0{tenant_id}\0{session_id}".encode()).hexdigest()
