"""In-memory SFU group-key repository (tests, restart and multi-Hub simulations).

Behaves like ``SqlSfuBroadcastGroupKeyRepository``; a shared
``InMemorySfuBroadcastGroupKeyStore`` models several Hub instances or a Hub
restart over the same persisted state.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace

from agent.models.sfu_group_keys import (
    SfuGroupKeyDeliveryPage,
    SfuGroupKeyEpochState,
    SfuGroupKeyMutationResult,
    SfuGroupKeyPackageDelivery,
    SfuGroupKeyPackageWrite,
    SfuGroupKeyReceipt,
    SfuHubSealedSecret,
)
from agent.ports.sfu_group_keys import SfuHubSecretEnvelopePort
from agent.repositories.sfu_broadcast_group_key_records import (
    _delivery_failure,
    _package_aad,
    _receipt_key,
    _validate_page,
    _validate_receipt,
)
from agent.repositories.sfu_broadcast_group_key_validation import (
    SfuBroadcastGroupKeyRepositoryError,
)
from agent.repositories.sfu_broadcast_group_key_validation import (
    mutation_result as _result,
)
from agent.repositories.sfu_broadcast_group_key_validation import (
    publisher_id as _publisher_id,
)
from agent.repositories.sfu_broadcast_group_key_validation import (
    same_packages as _same_packages,
)
from agent.repositories.sfu_broadcast_group_key_validation import (
    validate_packages as _validate_packages,
)
from agent.repositories.sfu_broadcast_group_key_validation import (
    validate_state as _validate_state,
)


@dataclass(frozen=True, slots=True)
class _StoredPackage:
    authorization_id: str
    tenant_id: str
    session_id: str
    recipient_id: str
    recipient_digest: str
    package_ref: str
    package_digest: str
    package_bytes: int
    envelope: SfuHubSealedSecret | None
    membership_epoch: int
    key_epoch: int
    status: str
    fencing_token: int
    version: int
    expires_at_ms: int


class InMemorySfuBroadcastGroupKeyStore:
    """Shareable store models restart and multi-Hub repository instances."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.states: dict[str, SfuGroupKeyEpochState] = {}
        self.packages: dict[str, _StoredPackage] = {}
        self.receipts: dict[tuple[str, str, str, str], SfuGroupKeyReceipt] = {}


class InMemorySfuBroadcastGroupKeyRepository:
    def __init__(
        self,
        envelope: SfuHubSecretEnvelopePort,
        *,
        store: InMemorySfuBroadcastGroupKeyStore | None = None,
    ) -> None:
        self._envelope = envelope
        self._store = store or InMemorySfuBroadcastGroupKeyStore()

    def receipt(
        self, *, tenant_id: str, actor_digest: str, operation: str,
        idempotency_key_digest: str, now_ms: int,
    ) -> SfuGroupKeyReceipt | None:
        with self._store.lock:
            value = self._store.receipts.get((tenant_id, actor_digest, operation, idempotency_key_digest))
            return value if value is not None and value.expires_at_ms > now_ms else None

    def latest(self, *, tenant_id: str, session_id: str, room_id: str) -> SfuGroupKeyEpochState | None:
        with self._store.lock:
            candidates = [
                state for state in self._store.states.values()
                if state.authorization.tenant_id == tenant_id
                and state.session_id == session_id
                and state.authorization.room_id == room_id
                and state.status != "tombstoned"
            ]
            return max(candidates, key=lambda state: state.authorization.epoch, default=None)

    def get(self, *, tenant_id: str, authorization_id: str) -> SfuGroupKeyEpochState | None:
        with self._store.lock:
            state = self._store.states.get(authorization_id)
            return state if state is not None and state.authorization.tenant_id == tenant_id else None

    def create_epoch(
        self, state: SfuGroupKeyEpochState, receipt: SfuGroupKeyReceipt,
        *, now_ms: int,
    ) -> SfuGroupKeyMutationResult:
        _validate_state(state)
        _validate_receipt(receipt, state.authorization.tenant_id)
        key = _receipt_key(receipt)
        with self._store.lock:
            replay = self._store.receipts.get(key)
            if replay is not None:
                if replay.request_digest != receipt.request_digest:
                    return _result("conflict", reason="sfu_group_idempotency_conflict")
                current = self._store.states.get(state.authorization.authorization_id)
                return _result("saved", state=current, replayed=True)
            if state.authorization.authorization_id in self._store.states:
                return _result("conflict", reason="sfu_group_authorization_conflict")
            for authorization_id, current in tuple(self._store.states.items()):
                if (
                    current.status == "active"
                    and current.authorization.tenant_id == state.authorization.tenant_id
                    and current.authorization.room_id == state.authorization.room_id
                    and current.authorization.publication_id == state.authorization.publication_id
                ):
                    self._store.states[authorization_id] = replace(
                        current, status="revoked", version=current.version + 1
                    )
                    self._destroy_packages(authorization_id, status="revoked")
            self._store.states[state.authorization.authorization_id] = state
            self._store.receipts[key] = receipt
            return _result("saved", state=state)

    def deliver(
        self, *, tenant_id: str, authorization_id: str, expected_version: int,
        expected_fencing_token: int, packages: tuple[SfuGroupKeyPackageWrite, ...],
        receipt: SfuGroupKeyReceipt, now_ms: int,
    ) -> SfuGroupKeyMutationResult:
        key = _receipt_key(receipt)
        with self._store.lock:
            replay = self._store.receipts.get(key)
            if replay is not None:
                if replay.request_digest != receipt.request_digest:
                    return _result("conflict", reason="sfu_group_idempotency_conflict")
                return _result("saved", state=self._store.states.get(authorization_id), replayed=True)
            state = self._store.states.get(authorization_id)
            failure = _delivery_failure(state, tenant_id, expected_version, expected_fencing_token, now_ms)
            if failure is not None:
                return failure
            assert state is not None
            validation = _validate_packages(state, packages, self._envelope)
            if validation is not None:
                return validation
            existing = [row for row in self._store.packages.values() if row.authorization_id == authorization_id]
            if existing:
                if not _same_packages(existing, packages):
                    return _result("conflict", state=state, reason="sfu_group_package_conflict")
            else:
                for package in packages:
                    aad = _package_aad(tenant_id, authorization_id, package.package_ref, package.package_digest)
                    envelope = self._envelope.seal(
                        package.opaque_package,
                        purpose="sfu-group-key-package",
                        scope=f"{tenant_id}:{authorization_id}",
                        aad=aad,
                    )
                    self._store.packages[package.package_ref] = _StoredPackage(
                        authorization_id, tenant_id, state.session_id, package.recipient_id,
                        package.recipient_digest, package.package_ref, package.package_digest,
                        len(package.opaque_package), envelope, state.authorization.membership_epoch or 0,
                        state.authorization.epoch, "delivered", state.fencing_token, 1,
                        package.expires_at_ms,
                    )
            saved = replace(
                state,
                package_count=len(packages),
                total_package_bytes=sum(len(package.opaque_package) for package in packages),
                delivered_member_ids=tuple(sorted(package.recipient_id for package in packages)),
                version=state.version + 1,
            )
            self._store.states[authorization_id] = saved
            self._store.receipts[key] = receipt
            return _result("saved", state=saved, replayed=bool(existing))

    def read_for_recipient(
        self, *, tenant_id: str, session_id: str, recipient_digest: str,
        membership_epoch: int, cursor: str, limit: int, now_ms: int,
    ) -> SfuGroupKeyDeliveryPage:
        _validate_page(limit)
        with self._store.lock:
            rows = sorted(
                (
                    row for row in self._store.packages.values()
                    if row.tenant_id == tenant_id and row.session_id == session_id
                    and row.recipient_digest == recipient_digest and row.membership_epoch == membership_epoch
                    and row.status in {"delivered", "acknowledged"}
                    and row.expires_at_ms > now_ms and row.package_ref > cursor
                ),
                key=lambda row: row.package_ref,
            )[:limit]
            deliveries = tuple(self._delivery(row) for row in rows)
            return SfuGroupKeyDeliveryPage(deliveries, rows[-1].package_ref if rows else cursor)

    def acknowledge(
        self, *, tenant_id: str, authorization_id: str, package_ref: str,
        recipient_digest: str, membership_epoch: int, now_ms: int,
    ) -> SfuGroupKeyMutationResult:
        with self._store.lock:
            state = self._store.states.get(authorization_id)
            if state is None or state.authorization.tenant_id != tenant_id:
                return _result("not_found", reason="sfu_group_authorization_unavailable")
            if state.status != "active" or state.authorization.expires_at_ms <= now_ms:
                return _result("expired", state=state, reason="sfu_group_authorization_stale")
            row = self._store.packages.get(package_ref)
            if (
                row is None or row.authorization_id != authorization_id
                or row.recipient_digest != recipient_digest or row.membership_epoch != membership_epoch
            ):
                return _result("not_found", state=state, reason="sfu_group_package_recipient_mismatch")
            if row.status == "acknowledged":
                return _result("saved", state=state, replayed=True)
            if row.status != "delivered" or row.expires_at_ms <= now_ms:
                return _result("expired", state=state, reason="sfu_group_package_expired")
            self._store.packages[package_ref] = replace(row, status="acknowledged", version=row.version + 1)
            acknowledged = tuple(sorted({*state.acknowledged_member_ids, row.recipient_id}))
            saved = replace(state, acknowledged_member_ids=acknowledged, version=state.version + 1)
            self._store.states[authorization_id] = saved
            return _result("saved", state=saved)

    def purge_expired(self, *, now_ms: int, limit: int) -> int:
        _validate_page(limit)
        with self._store.lock:
            expired = sorted(
                (state for state in self._store.states.values() if state.authorization.expires_at_ms <= now_ms),
                key=lambda state: (state.authorization.expires_at_ms, state.authorization.authorization_id),
            )[:limit]
            for state in expired:
                self._destroy_packages(state.authorization.authorization_id, status="tombstoned")
                self._store.states[state.authorization.authorization_id] = replace(
                    state, status="tombstoned", delivered_member_ids=(),
                    acknowledged_member_ids=(), version=state.version + 1,
                )
            for key, receipt in tuple(self._store.receipts.items()):
                if receipt.expires_at_ms <= now_ms:
                    self._store.receipts.pop(key, None)
            return len(expired)

    def rotate_envelopes(self, *, limit: int) -> int:
        _validate_page(limit)
        rotated = 0
        with self._store.lock:
            for package_ref, row in tuple(self._store.packages.items()):
                if rotated >= limit:
                    break
                if row.envelope is None or row.envelope.key_id == self._envelope.active_key_id:
                    continue
                aad = _package_aad(row.tenant_id, row.authorization_id, row.package_ref, row.package_digest)
                envelope = self._envelope.rewrap(
                    row.envelope, purpose="sfu-group-key-package",
                    scope=f"{row.tenant_id}:{row.authorization_id}", aad=aad,
                )
                self._store.packages[package_ref] = replace(row, envelope=envelope, version=row.version + 1)
                rotated += 1
        return rotated

    def _delivery(self, row: _StoredPackage) -> SfuGroupKeyPackageDelivery:
        state = self._store.states.get(row.authorization_id)
        if state is None or row.envelope is None:
            raise SfuBroadcastGroupKeyRepositoryError("sfu_group_package_unavailable")
        aad = _package_aad(row.tenant_id, row.authorization_id, row.package_ref, row.package_digest)
        opaque = self._envelope.open(
            row.envelope, purpose="sfu-group-key-package",
            scope=f"{row.tenant_id}:{row.authorization_id}", aad=aad,
        )
        return SfuGroupKeyPackageDelivery(
            state.authorization,
            _publisher_id(
                state.authorization,
                state.session_id,
                state.publisher_digest,
                self._envelope,
            ),
            row.package_ref,
            opaque,
            row.package_digest,
            row.expires_at_ms,
        )

    def _destroy_packages(self, authorization_id: str, *, status: str) -> None:
        for package_ref, row in tuple(self._store.packages.items()):
            if row.authorization_id == authorization_id:
                self._store.packages[package_ref] = replace(
                    row, envelope=None, status=status, version=row.version + 1
                )


__all__ = [
    "InMemorySfuBroadcastGroupKeyRepository",
    "InMemorySfuBroadcastGroupKeyStore",
]
