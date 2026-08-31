"""Persistent adapters for bounded, epoch-fenced SFU group-key delivery."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, replace

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models.sfu_broadcast_group_keys import (
    SfuBroadcastGroupKeyAuthorizationDB,
    SfuBroadcastGroupKeyPackageDB,
    SfuBroadcastGroupKeyReceiptDB,
)
from agent.repositories.sfu_broadcast_group_key_validation import (
    MAX_GROUP_KEY_PACKAGE_BYTES,
    MAX_GROUP_KEY_PACKAGES,
    MAX_GROUP_KEY_TOTAL_BYTES,
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
from agent.services.sfu_broadcast_group_key_repository_port import (
    SfuGroupKeyDeliveryPage,
    SfuGroupKeyEpochState,
    SfuGroupKeyMutationResult,
    SfuGroupKeyPackageDelivery,
    SfuGroupKeyPackageWrite,
    SfuGroupKeyReceipt,
)
from agent.services.sfu_hub_secret_envelope import (
    SfuHubSealedSecret,
    SfuHubSecretEnvelopeError,
    SfuHubSecretEnvelopePort,
)
from agent.services.webrtc_group_key_authorization_service import GroupKeyEpochAuthorization


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


class SqlSfuBroadcastGroupKeyRepository:
    def __init__(
        self,
        envelope: SfuHubSecretEnvelopePort,
        *,
        db_engine=default_engine,
    ) -> None:
        self._envelope = envelope
        self._engine = db_engine

    def receipt(
        self, *, tenant_id: str, actor_digest: str, operation: str,
        idempotency_key_digest: str, now_ms: int,
    ) -> SfuGroupKeyReceipt | None:
        try:
            with Session(self._engine) as db:
                row = db.exec(select(SfuBroadcastGroupKeyReceiptDB).where(
                    SfuBroadcastGroupKeyReceiptDB.tenant_id == tenant_id,
                    SfuBroadcastGroupKeyReceiptDB.actor_digest == actor_digest,
                    SfuBroadcastGroupKeyReceiptDB.operation == operation,
                    SfuBroadcastGroupKeyReceiptDB.idempotency_key_digest == idempotency_key_digest,
                    SfuBroadcastGroupKeyReceiptDB.expires_at_ms > now_ms,
                )).first()
                return _receipt_from_row(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise SfuBroadcastGroupKeyRepositoryError("sfu_group_key_store_unavailable") from exc

    def latest(self, *, tenant_id: str, session_id: str, room_id: str) -> SfuGroupKeyEpochState | None:
        try:
            with Session(self._engine) as db:
                row = db.exec(select(SfuBroadcastGroupKeyAuthorizationDB).where(
                    SfuBroadcastGroupKeyAuthorizationDB.tenant_id == tenant_id,
                    SfuBroadcastGroupKeyAuthorizationDB.session_id == session_id,
                    SfuBroadcastGroupKeyAuthorizationDB.room_id == room_id,
                    SfuBroadcastGroupKeyAuthorizationDB.status != "tombstoned",
                ).order_by(SfuBroadcastGroupKeyAuthorizationDB.key_epoch.desc()).limit(1)).first()
                return self._state(db, row) if row is not None else None
        except SQLAlchemyError as exc:
            raise SfuBroadcastGroupKeyRepositoryError("sfu_group_key_store_unavailable") from exc

    def get(self, *, tenant_id: str, authorization_id: str) -> SfuGroupKeyEpochState | None:
        try:
            with Session(self._engine) as db:
                row = db.exec(select(SfuBroadcastGroupKeyAuthorizationDB).where(
                    SfuBroadcastGroupKeyAuthorizationDB.id == authorization_id,
                    SfuBroadcastGroupKeyAuthorizationDB.tenant_id == tenant_id,
                    SfuBroadcastGroupKeyAuthorizationDB.status != "tombstoned",
                )).first()
                return self._state(db, row) if row is not None else None
        except SQLAlchemyError as exc:
            raise SfuBroadcastGroupKeyRepositoryError("sfu_group_key_store_unavailable") from exc

    def create_epoch(
        self, state: SfuGroupKeyEpochState, receipt: SfuGroupKeyReceipt,
        *, now_ms: int,
    ) -> SfuGroupKeyMutationResult:
        _validate_state(state)
        _validate_receipt(receipt, state.authorization.tenant_id)
        try:
            with Session(self._engine) as db:
                replay = _find_receipt(db, receipt)
                if replay is not None:
                    if replay.request_digest != receipt.request_digest:
                        return _result("conflict", reason="sfu_group_idempotency_conflict")
                    existing = db.get(SfuBroadcastGroupKeyAuthorizationDB, state.authorization.authorization_id)
                    return _result("saved", state=self._state(db, existing) if existing else None, replayed=True)
                active = db.exec(select(SfuBroadcastGroupKeyAuthorizationDB).where(
                    SfuBroadcastGroupKeyAuthorizationDB.tenant_id == state.authorization.tenant_id,
                    SfuBroadcastGroupKeyAuthorizationDB.room_id == state.authorization.room_id,
                    SfuBroadcastGroupKeyAuthorizationDB.publication_id == state.authorization.publication_id,
                    SfuBroadcastGroupKeyAuthorizationDB.status == "active",
                )).all()
                now = now_ms / 1000.0
                for current in active:
                    current.status = "revoked"
                    current.version += 1
                    current.updated_at = now
                    db.add(current)
                    db.exec(sa.update(SfuBroadcastGroupKeyPackageDB).where(
                        SfuBroadcastGroupKeyPackageDB.authorization_id == current.id,
                    ).values(
                        status="revoked", sealed_package=None, wrapping_nonce=None,
                        wrapping_key_id=None, version=SfuBroadcastGroupKeyPackageDB.version + 1,
                        updated_at=now,
                    ))
                db.flush()
                db.add(_authorization_row(state, now, self._envelope))
                db.add(_receipt_row(receipt))
                db.commit()
                return _result("saved", state=state)
        except IntegrityError:
            return _result("conflict", reason="sfu_group_authorization_conflict")
        except SQLAlchemyError as exc:
            raise SfuBroadcastGroupKeyRepositoryError("sfu_group_key_store_unavailable") from exc

    def deliver(
        self, *, tenant_id: str, authorization_id: str, expected_version: int,
        expected_fencing_token: int, packages: tuple[SfuGroupKeyPackageWrite, ...],
        receipt: SfuGroupKeyReceipt, now_ms: int,
    ) -> SfuGroupKeyMutationResult:
        try:
            with Session(self._engine) as db:
                replay = _find_receipt(db, receipt)
                row = db.get(SfuBroadcastGroupKeyAuthorizationDB, authorization_id)
                state = self._state(db, row) if row is not None and row.tenant_id == tenant_id else None
                if replay is not None:
                    if replay.request_digest != receipt.request_digest:
                        return _result("conflict", reason="sfu_group_idempotency_conflict")
                    return _result("saved", state=state, replayed=True)
                failure = _delivery_failure(state, tenant_id, expected_version, expected_fencing_token, now_ms)
                if failure is not None:
                    return failure
                assert row is not None and state is not None
                validation = _validate_packages(state, packages, self._envelope)
                if validation is not None:
                    return validation
                existing = db.exec(select(SfuBroadcastGroupKeyPackageDB).where(
                    SfuBroadcastGroupKeyPackageDB.authorization_id == authorization_id
                )).all()
                if existing:
                    existing_shape = {
                        (item.id, item.recipient_digest, item.package_digest, item.package_bytes)
                        for item in existing
                    }
                    desired_shape = {
                        (
                            item.package_ref,
                            item.recipient_digest,
                            item.package_digest,
                            len(item.opaque_package),
                        )
                        for item in packages
                    }
                    if existing_shape != desired_shape:
                        return _result("conflict", state=state, reason="sfu_group_package_conflict")
                else:
                    for package in packages:
                        aad = _package_aad(tenant_id, authorization_id, package.package_ref, package.package_digest)
                        envelope = self._envelope.seal(
                            package.opaque_package, purpose="sfu-group-key-package",
                            scope=f"{tenant_id}:{authorization_id}", aad=aad,
                        )
                        db.add(SfuBroadcastGroupKeyPackageDB(
                            id=package.package_ref, authorization_id=authorization_id,
                            tenant_id=tenant_id, session_id=state.session_id,
                        recipient_digest=package.recipient_digest,
                        recipient_digest_key_id=self._envelope.active_blind_key_id,
                            package_digest=package.package_digest,
                            package_bytes=len(package.opaque_package),
                            sealed_package=envelope.ciphertext, wrapping_nonce=envelope.nonce,
                            wrapping_key_id=envelope.key_id,
                            membership_epoch=state.authorization.membership_epoch or 0,
                            key_epoch=state.authorization.epoch, status="delivered",
                            fencing_token=state.fencing_token, version=1,
                            expires_at_ms=package.expires_at_ms,
                            delivered_at=now_ms / 1000.0, updated_at=now_ms / 1000.0,
                        ))
                row.package_count = len(packages)
                row.total_package_bytes = sum(len(package.opaque_package) for package in packages)
                row.version += 1
                row.updated_at = now_ms / 1000.0
                db.add(row)
                db.add(_receipt_row(receipt))
                db.commit()
                current = self.get(tenant_id=tenant_id, authorization_id=authorization_id)
                return _result("saved", state=current, replayed=bool(existing))
        except IntegrityError:
            return _result("conflict", reason="sfu_group_package_conflict")
        except (SQLAlchemyError, SfuHubSecretEnvelopeError) as exc:
            raise SfuBroadcastGroupKeyRepositoryError("sfu_group_key_store_unavailable") from exc

    def read_for_recipient(
        self, *, tenant_id: str, session_id: str, recipient_digest: str,
        membership_epoch: int, cursor: str, limit: int, now_ms: int,
    ) -> SfuGroupKeyDeliveryPage:
        _validate_page(limit)
        try:
            with Session(self._engine) as db:
                rows = db.exec(select(SfuBroadcastGroupKeyPackageDB).where(
                    SfuBroadcastGroupKeyPackageDB.tenant_id == tenant_id,
                    SfuBroadcastGroupKeyPackageDB.session_id == session_id,
                    SfuBroadcastGroupKeyPackageDB.recipient_digest == recipient_digest,
                    SfuBroadcastGroupKeyPackageDB.membership_epoch == membership_epoch,
                    SfuBroadcastGroupKeyPackageDB.status.in_(("delivered", "acknowledged")),
                    SfuBroadcastGroupKeyPackageDB.expires_at_ms > now_ms,
                    SfuBroadcastGroupKeyPackageDB.id > cursor,
                ).order_by(SfuBroadcastGroupKeyPackageDB.id).limit(limit)).all()
                deliveries: list[SfuGroupKeyPackageDelivery] = []
                for row in rows:
                    authorization = db.get(SfuBroadcastGroupKeyAuthorizationDB, row.authorization_id)
                    if (
                        authorization is None
                        or authorization.status != "active"
                        or authorization.expires_at_ms <= now_ms
                    ):
                        continue
                    if row.sealed_package is None or row.wrapping_nonce is None or not row.wrapping_key_id:
                        continue
                    envelope = SfuHubSealedSecret(row.wrapping_key_id, row.wrapping_nonce, row.sealed_package)
                    aad = _package_aad(tenant_id, row.authorization_id, row.id, row.package_digest)
                    opaque = self._envelope.open(
                        envelope, purpose="sfu-group-key-package",
                        scope=f"{tenant_id}:{row.authorization_id}", aad=aad,
                    )
                    deliveries.append(SfuGroupKeyPackageDelivery(
                        _authorization_from_row(authorization, self._envelope),
                        _publisher_id(
                            _authorization_from_row(authorization, self._envelope),
                            authorization.session_id,
                            authorization.publisher_digest,
                            self._envelope,
                        ),
                        row.id, opaque, row.package_digest, row.expires_at_ms,
                    ))
                next_cursor = rows[-1].id if rows else cursor
                return SfuGroupKeyDeliveryPage(tuple(deliveries), next_cursor)
        except (SQLAlchemyError, SfuHubSecretEnvelopeError) as exc:
            raise SfuBroadcastGroupKeyRepositoryError("sfu_group_key_store_unavailable") from exc

    def acknowledge(
        self, *, tenant_id: str, authorization_id: str, package_ref: str,
        recipient_digest: str, membership_epoch: int, now_ms: int,
    ) -> SfuGroupKeyMutationResult:
        try:
            with Session(self._engine) as db:
                authorization = db.get(SfuBroadcastGroupKeyAuthorizationDB, authorization_id)
                if authorization is None or authorization.tenant_id != tenant_id:
                    return _result("not_found", reason="sfu_group_authorization_unavailable")
                state = self._state(db, authorization)
                if authorization.status != "active" or authorization.expires_at_ms <= now_ms:
                    return _result("expired", state=state, reason="sfu_group_authorization_stale")
                package = db.exec(select(SfuBroadcastGroupKeyPackageDB).where(
                    SfuBroadcastGroupKeyPackageDB.id == package_ref,
                    SfuBroadcastGroupKeyPackageDB.authorization_id == authorization_id,
                    SfuBroadcastGroupKeyPackageDB.recipient_digest == recipient_digest,
                    SfuBroadcastGroupKeyPackageDB.membership_epoch == membership_epoch,
                )).first()
                if package is None:
                    return _result("not_found", state=state, reason="sfu_group_package_recipient_mismatch")
                if package.status == "acknowledged":
                    return _result("saved", state=state, replayed=True)
                if package.status != "delivered" or package.expires_at_ms <= now_ms:
                    return _result("expired", state=state, reason="sfu_group_package_expired")
                result = db.exec(sa.update(SfuBroadcastGroupKeyPackageDB).where(
                    SfuBroadcastGroupKeyPackageDB.id == package_ref,
                    SfuBroadcastGroupKeyPackageDB.version == package.version,
                    SfuBroadcastGroupKeyPackageDB.status == "delivered",
                ).values(
                    status="acknowledged", acknowledged_at=now_ms / 1000.0,
                    version=SfuBroadcastGroupKeyPackageDB.version + 1,
                    updated_at=now_ms / 1000.0,
                ))
                if int(result.rowcount or 0) != 1:
                    db.rollback()
                    return _result("conflict", state=state, reason="sfu_group_ack_conflict")
                authorization_result = db.exec(
                    sa.update(SfuBroadcastGroupKeyAuthorizationDB)
                    .where(
                        SfuBroadcastGroupKeyAuthorizationDB.id == authorization.id,
                        SfuBroadcastGroupKeyAuthorizationDB.version == authorization.version,
                        SfuBroadcastGroupKeyAuthorizationDB.fencing_token
                        == authorization.fencing_token,
                    )
                    .values(
                        version=SfuBroadcastGroupKeyAuthorizationDB.version + 1,
                        updated_at=now_ms / 1000.0,
                    )
                )
                if int(authorization_result.rowcount or 0) != 1:
                    db.rollback()
                    return _result(
                        "conflict",
                        state=state,
                        reason="sfu_group_authorization_version_conflict",
                    )
                db.commit()
                return _result("saved", state=self.get(tenant_id=tenant_id, authorization_id=authorization_id))
        except SQLAlchemyError as exc:
            raise SfuBroadcastGroupKeyRepositoryError("sfu_group_key_store_unavailable") from exc

    def purge_expired(self, *, now_ms: int, limit: int) -> int:
        _validate_page(limit)
        try:
            with Session(self._engine) as db:
                rows = db.exec(select(SfuBroadcastGroupKeyAuthorizationDB).where(
                    SfuBroadcastGroupKeyAuthorizationDB.expires_at_ms <= now_ms,
                    SfuBroadcastGroupKeyAuthorizationDB.status != "tombstoned",
                ).order_by(
                    SfuBroadcastGroupKeyAuthorizationDB.expires_at_ms,
                    SfuBroadcastGroupKeyAuthorizationDB.id,
                ).limit(limit)).all()
                ids = [row.id for row in rows]
                if ids:
                    db.exec(sa.update(SfuBroadcastGroupKeyPackageDB).where(
                        SfuBroadcastGroupKeyPackageDB.authorization_id.in_(ids)
                    ).values(
                        status="tombstoned", sealed_package=None, wrapping_nonce=None,
                        wrapping_key_id=None, version=SfuBroadcastGroupKeyPackageDB.version + 1,
                        updated_at=now_ms / 1000.0,
                    ))
                    db.exec(sa.update(SfuBroadcastGroupKeyAuthorizationDB).where(
                        SfuBroadcastGroupKeyAuthorizationDB.id.in_(ids)
                    ).values(
                        status="tombstoned", authorization_json={}, tombstoned_at=now_ms / 1000.0,
                        version=SfuBroadcastGroupKeyAuthorizationDB.version + 1,
                        updated_at=now_ms / 1000.0,
                    ))
                db.exec(sa.delete(SfuBroadcastGroupKeyReceiptDB).where(
                    SfuBroadcastGroupKeyReceiptDB.expires_at_ms <= now_ms
                ))
                db.commit()
                return len(rows)
        except SQLAlchemyError as exc:
            raise SfuBroadcastGroupKeyRepositoryError("sfu_group_key_store_unavailable") from exc

    def rotate_envelopes(self, *, limit: int) -> int:
        _validate_page(limit)
        try:
            with Session(self._engine) as db:
                rows = db.exec(select(SfuBroadcastGroupKeyPackageDB).where(
                    SfuBroadcastGroupKeyPackageDB.wrapping_key_id.is_not(None),
                    SfuBroadcastGroupKeyPackageDB.status.in_(("delivered", "acknowledged")),
                    sa.or_(
                        SfuBroadcastGroupKeyPackageDB.wrapping_key_id
                        != self._envelope.active_key_id,
                        SfuBroadcastGroupKeyPackageDB.recipient_digest_key_id.is_(None),
                        SfuBroadcastGroupKeyPackageDB.recipient_digest_key_id
                        != self._envelope.active_blind_key_id,
                    ),
                ).order_by(SfuBroadcastGroupKeyPackageDB.id).limit(limit)).all()
                for row in rows:
                    if row.sealed_package is None or row.wrapping_nonce is None or not row.wrapping_key_id:
                        continue
                    if row.wrapping_key_id != self._envelope.active_key_id:
                        current = SfuHubSealedSecret(row.wrapping_key_id, row.wrapping_nonce, row.sealed_package)
                        aad = _package_aad(row.tenant_id, row.authorization_id, row.id, row.package_digest)
                        rotated = self._envelope.rewrap(
                            current, purpose="sfu-group-key-package",
                            scope=f"{row.tenant_id}:{row.authorization_id}", aad=aad,
                        )
                        row.sealed_package = rotated.ciphertext
                        row.wrapping_nonce = rotated.nonce
                        row.wrapping_key_id = rotated.key_id
                    authorization_row = db.get(
                        SfuBroadcastGroupKeyAuthorizationDB, row.authorization_id
                    )
                    if authorization_row is None:
                        continue
                    authorization = _authorization_from_row(
                        authorization_row, self._envelope
                    )
                    member_id = next(
                        (
                            member
                            for member, package_ref in authorization.key_package_refs.items()
                            if package_ref == row.id
                        ),
                        None,
                    )
                    if member_id is None:
                        continue
                    candidates = self._envelope.blind_candidates(
                        purpose="sfu-group-key-subject",
                        scope=f"{row.tenant_id}:{row.session_id}",
                        value=member_id,
                    )
                    if row.recipient_digest not in {item.digest for item in candidates}:
                        continue
                    active_index = self._envelope.blind_index(
                        purpose="sfu-group-key-subject",
                        scope=f"{row.tenant_id}:{row.session_id}",
                        value=member_id,
                    )
                    row.recipient_digest = active_index.digest
                    row.recipient_digest_key_id = active_index.key_id
                    row.version += 1
                    row.updated_at = time.time()
                    db.add(row)
                authorization_rows = db.exec(
                    select(SfuBroadcastGroupKeyAuthorizationDB)
                    .where(
                        sa.or_(
                            SfuBroadcastGroupKeyAuthorizationDB.authorization_wrapping_key_id.is_(None),
                            SfuBroadcastGroupKeyAuthorizationDB.authorization_wrapping_key_id
                            != self._envelope.active_key_id,
                        )
                    )
                    .order_by(SfuBroadcastGroupKeyAuthorizationDB.id)
                    .limit(limit)
                ).all()
                authorization_changes = 0
                for authorization_row in authorization_rows:
                    aad = _authorization_aad(
                        authorization_row.tenant_id,
                        authorization_row.id,
                        authorization_row.member_set_digest,
                    )
                    scope = f"{authorization_row.tenant_id}:{authorization_row.id}"
                    if (
                        authorization_row.authorization_ciphertext is not None
                        and authorization_row.authorization_nonce is not None
                        and authorization_row.authorization_wrapping_key_id
                    ):
                        if (
                            authorization_row.authorization_wrapping_key_id
                            == self._envelope.active_key_id
                        ):
                            continue
                        rotated_authorization = self._envelope.rewrap(
                            SfuHubSealedSecret(
                                authorization_row.authorization_wrapping_key_id,
                                authorization_row.authorization_nonce,
                                authorization_row.authorization_ciphertext,
                            ),
                            purpose="sfu-group-key-authorization",
                            scope=scope,
                            aad=aad,
                        )
                    elif authorization_row.authorization_json:
                        rotated_authorization = self._envelope.seal(
                            json.dumps(
                                authorization_row.authorization_json,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8"),
                            purpose="sfu-group-key-authorization",
                            scope=scope,
                            aad=aad,
                        )
                    else:
                        continue
                    authorization_row.authorization_ciphertext = (
                        rotated_authorization.ciphertext
                    )
                    authorization_row.authorization_nonce = rotated_authorization.nonce
                    authorization_row.authorization_wrapping_key_id = (
                        rotated_authorization.key_id
                    )
                    authorization_row.authorization_json = {}
                    authorization_row.version += 1
                    authorization_row.updated_at = time.time()
                    db.add(authorization_row)
                    authorization_changes += 1
                db.commit()
                return len(rows) + authorization_changes
        except (SQLAlchemyError, SfuHubSecretEnvelopeError) as exc:
            raise SfuBroadcastGroupKeyRepositoryError("sfu_group_key_store_unavailable") from exc

    def _state(
        self, db: Session, row: SfuBroadcastGroupKeyAuthorizationDB | None,
    ) -> SfuGroupKeyEpochState | None:
        if row is None or (
            not row.authorization_json and row.authorization_ciphertext is None
        ):
            return None
        authorization = _authorization_from_row(row, self._envelope)
        packages = db.exec(select(SfuBroadcastGroupKeyPackageDB).where(
            SfuBroadcastGroupKeyPackageDB.authorization_id == row.id,
            SfuBroadcastGroupKeyPackageDB.status.in_(("delivered", "acknowledged")),
        )).all()
        delivered_digests = {package.recipient_digest for package in packages}
        ack_digests = {package.recipient_digest for package in packages if package.status == "acknowledged"}
        member_digests = {
            candidate.digest: member
            for member in authorization.member_ids
            for candidate in self._envelope.blind_candidates(
                purpose="sfu-group-key-subject",
                scope=f"{row.tenant_id}:{row.session_id}",
                value=member,
            )
        }
        return SfuGroupKeyEpochState(
            authorization=authorization,
            session_id=row.session_id,
            publisher_digest=row.publisher_digest,
            distribution_mode="bounded_rewrap",
            status=row.status,
            package_count=row.package_count,
            total_package_bytes=row.total_package_bytes,
            delivered_member_ids=tuple(
                sorted(
                    member_digests[digest]
                    for digest in delivered_digests
                    if digest in member_digests
                )
            ),
            acknowledged_member_ids=tuple(
                sorted(
                    member_digests[digest]
                    for digest in ack_digests
                    if digest in member_digests
                )
            ),
            fencing_token=row.fencing_token,
            version=row.version,
        )


def _delivery_failure(
    state: SfuGroupKeyEpochState | None,
    tenant_id: str,
    expected_version: int,
    expected_fencing_token: int,
    now_ms: int,
) -> SfuGroupKeyMutationResult | None:
    if state is None or state.authorization.tenant_id != tenant_id:
        return _result("not_found", reason="sfu_group_authorization_unavailable")
    if state.status != "active" or state.authorization.expires_at_ms <= now_ms:
        return _result("expired", state=state, reason="sfu_group_authorization_stale")
    if state.version != expected_version:
        return _result("conflict", state=state, reason="sfu_group_version_conflict")
    if state.fencing_token != expected_fencing_token:
        return _result("stale_epoch", state=state, reason="sfu_group_fencing_stale")
    return None


def _authorization_row(
    state: SfuGroupKeyEpochState,
    now: float,
    envelope: SfuHubSecretEnvelopePort,
) -> SfuBroadcastGroupKeyAuthorizationDB:
    authorization = state.authorization
    payload = json.dumps(
        _authorization_json(authorization), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    sealed = envelope.seal(
        payload,
        purpose="sfu-group-key-authorization",
        scope=f"{authorization.tenant_id}:{authorization.authorization_id}",
        aad=_authorization_aad(
            authorization.tenant_id,
            authorization.authorization_id,
            authorization.member_set_digest,
        ),
    )
    return SfuBroadcastGroupKeyAuthorizationDB(
        id=authorization.authorization_id,
        tenant_id=authorization.tenant_id,
        session_id=state.session_id,
        room_id=authorization.room_id,
        publication_id=authorization.publication_id,
        publisher_digest=state.publisher_digest,
        membership_epoch=authorization.membership_epoch or 0,
        key_epoch=authorization.epoch,
        previous_key_epoch=authorization.previous_epoch,
        member_set_digest=authorization.member_set_digest,
        authorization_json={},
        authorization_ciphertext=sealed.ciphertext,
        authorization_nonce=sealed.nonce,
        authorization_wrapping_key_id=sealed.key_id,
        distribution_mode=state.distribution_mode,
        package_count=state.package_count,
        total_package_bytes=state.total_package_bytes,
        status=state.status,
        fencing_token=state.fencing_token,
        version=state.version,
        valid_from_ms=authorization.valid_from_ms,
        expires_at_ms=authorization.expires_at_ms,
        rekey_deadline_ms=authorization.rekey_deadline_ms,
        created_at=now,
        updated_at=now,
    )


def _authorization_json(value: GroupKeyEpochAuthorization) -> dict:
    raw = asdict(value)
    raw["member_ids"] = list(value.member_ids)
    return json.loads(json.dumps(raw))


def _authorization_from_json(raw: dict) -> GroupKeyEpochAuthorization:
    value = dict(raw)
    value["member_ids"] = tuple(value.get("member_ids") or ())
    value["key_package_refs"] = dict(value.get("key_package_refs") or {})
    return GroupKeyEpochAuthorization(**value)


def _authorization_from_row(
    row: SfuBroadcastGroupKeyAuthorizationDB,
    envelope: SfuHubSecretEnvelopePort,
) -> GroupKeyEpochAuthorization:
    if (
        row.authorization_ciphertext is not None
        and row.authorization_nonce is not None
        and row.authorization_wrapping_key_id
    ):
        plaintext = envelope.open(
            SfuHubSealedSecret(
                row.authorization_wrapping_key_id,
                row.authorization_nonce,
                row.authorization_ciphertext,
            ),
            purpose="sfu-group-key-authorization",
            scope=f"{row.tenant_id}:{row.id}",
            aad=_authorization_aad(row.tenant_id, row.id, row.member_set_digest),
        )
        return _authorization_from_json(json.loads(plaintext.decode("utf-8")))
    return _authorization_from_json(row.authorization_json)


def _authorization_aad(
    tenant_id: str, authorization_id: str, member_set_digest: str
) -> bytes:
    return json.dumps(
        {
            "authorization_id": authorization_id,
            "member_set_digest": member_set_digest,
            "tenant_id": tenant_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _receipt_row(receipt: SfuGroupKeyReceipt) -> SfuBroadcastGroupKeyReceiptDB:
    return SfuBroadcastGroupKeyReceiptDB(
        id="sfu-gk-receipt-" + hashlib.sha256("\0".join(_receipt_key(receipt)).encode()).hexdigest(),
        tenant_id=receipt.tenant_id,
        actor_digest=receipt.actor_digest,
        operation=receipt.operation,
        idempotency_key_digest=receipt.idempotency_key_digest,
        request_digest=receipt.request_digest,
        result_json=json.loads(json.dumps(receipt.result)),
        expires_at_ms=receipt.expires_at_ms,
    )


def _receipt_from_row(row: SfuBroadcastGroupKeyReceiptDB) -> SfuGroupKeyReceipt:
    return SfuGroupKeyReceipt(
        row.tenant_id, row.actor_digest, row.operation, row.idempotency_key_digest,
        row.request_digest, json.loads(json.dumps(row.result_json)), row.expires_at_ms,
    )


def _find_receipt(db: Session, receipt: SfuGroupKeyReceipt) -> SfuBroadcastGroupKeyReceiptDB | None:
    return db.exec(select(SfuBroadcastGroupKeyReceiptDB).where(
        SfuBroadcastGroupKeyReceiptDB.tenant_id == receipt.tenant_id,
        SfuBroadcastGroupKeyReceiptDB.actor_digest == receipt.actor_digest,
        SfuBroadcastGroupKeyReceiptDB.operation == receipt.operation,
        SfuBroadcastGroupKeyReceiptDB.idempotency_key_digest == receipt.idempotency_key_digest,
    )).first()


def _receipt_key(receipt: SfuGroupKeyReceipt) -> tuple[str, str, str, str]:
    return receipt.tenant_id, receipt.actor_digest, receipt.operation, receipt.idempotency_key_digest


def _validate_receipt(receipt: SfuGroupKeyReceipt, tenant_id: str) -> None:
    if receipt.tenant_id != tenant_id or receipt.operation not in {"prepare", "deliver"}:
        raise SfuBroadcastGroupKeyRepositoryError("sfu_group_key_receipt_invalid")


def _package_aad(tenant_id: str, authorization_id: str, package_ref: str, package_digest: str) -> bytes:
    return f"ananta:sfu-group-key-package:v1\0{tenant_id}\0{authorization_id}\0{package_ref}\0{package_digest}".encode()


def _validate_page(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise SfuBroadcastGroupKeyRepositoryError("sfu_group_key_page_limit_invalid")


__all__ = [
    "InMemorySfuBroadcastGroupKeyRepository",
    "InMemorySfuBroadcastGroupKeyStore",
    "MAX_GROUP_KEY_PACKAGES",
    "MAX_GROUP_KEY_PACKAGE_BYTES",
    "MAX_GROUP_KEY_TOTAL_BYTES",
    "SfuBroadcastGroupKeyRepositoryError",
    "SqlSfuBroadcastGroupKeyRepository",
]
