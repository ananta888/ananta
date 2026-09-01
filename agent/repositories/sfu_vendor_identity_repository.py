"""Persistent CAS adapters for Hub-owned opaque SFU vendor identities."""

from __future__ import annotations

import threading
import time
from dataclasses import replace

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models.sfu_broadcast_vendor_identities import (
    SfuBroadcastDestinationHandleDB,
    SfuBroadcastVendorIdentityDB,
)
from agent.models.sfu_group_keys import SfuHubSealedSecret
from agent.services.sfu_vendor_identity_service import (
    SfuVendorDestinationBinding,
    SfuVendorIdentityBinding,
    SfuVendorIdentityError,
    SfuVendorIdentityMutationResult,
)


class InMemorySfuVendorIdentityStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.identities: dict[str, SfuVendorIdentityBinding] = {}
        self.destinations: dict[str, SfuVendorDestinationBinding] = {}


class InMemorySfuVendorIdentityRepository:
    def __init__(self, *, store: InMemorySfuVendorIdentityStore | None = None) -> None:
        self._store = store or InMemorySfuVendorIdentityStore()

    def active_for_membership(
        self, *, tenant_id: str, room_id: str, membership_digest: str,
        membership_epoch: int, identity_epoch: int,
        membership_digest_candidates: tuple[str, ...] = (),
        membership_digest_key_id: str | None = None,
    ) -> SfuVendorIdentityBinding | None:
        with self._store.lock:
            return next((
                value for value in self._store.identities.values()
                if value.tenant_id == tenant_id and value.room_id == room_id
                and value.membership_digest in {
                    membership_digest, *membership_digest_candidates
                }
                and value.membership_epoch == membership_epoch
                and value.identity_epoch == identity_epoch and value.status == "active"
            ), None)

    def get_identity(self, *, tenant_id: str, room_id: str, identity_handle: str) -> SfuVendorIdentityBinding | None:
        with self._store.lock:
            value = self._store.identities.get(identity_handle)
            return value if value and value.tenant_id == tenant_id and value.room_id == room_id else None

    def save_identity(
        self, binding: SfuVendorIdentityBinding, *, expected_version: int,
    ) -> SfuVendorIdentityMutationResult:
        _validate_identity(binding)
        with self._store.lock:
            current = self._store.identities.get(binding.identity_handle)
            if current is not None:
                return _result("conflict", identity=current, reason="sfu_vendor_identity_handle_collision")
            if expected_version != 0:
                return _result("conflict", reason="sfu_vendor_identity_version_conflict")
            conflict = self.active_for_membership(
                tenant_id=binding.tenant_id, room_id=binding.room_id,
                membership_digest=binding.membership_digest,
                membership_epoch=binding.membership_epoch, identity_epoch=binding.identity_epoch,
            )
            if conflict is not None:
                return _result("saved", identity=conflict, replayed=True)
            self._store.identities[binding.identity_handle] = binding
            return _result("saved", identity=binding)

    def save_destination(
        self, binding: SfuVendorDestinationBinding,
    ) -> SfuVendorIdentityMutationResult:
        _validate_destination(binding)
        with self._store.lock:
            identity = self._store.identities.get(binding.identity_handle)
            if identity is None or identity.status != "active":
                return _result("not_found", reason="sfu_vendor_identity_stale")
            replay = next((
                value for value in self._store.destinations.values()
                if value.identity_handle == binding.identity_handle
                and value.route_digest == binding.route_digest
                and value.publication_digest == binding.publication_digest
                and value.audience_digest == binding.audience_digest
                and value.route_epoch == binding.route_epoch and value.key_epoch == binding.key_epoch
                and value.status == "active"
            ), None)
            if replay is not None:
                return _result("saved", destination=replay, replayed=True)
            if binding.destination_handle in self._store.destinations:
                return _result("conflict", reason="sfu_destination_handle_collision")
            self._store.destinations[binding.destination_handle] = binding
            return _result("saved", destination=binding)

    def get_destination(
        self, *, tenant_id: str, room_id: str, destination_handle: str,
    ) -> SfuVendorDestinationBinding | None:
        with self._store.lock:
            value = self._store.destinations.get(destination_handle)
            return value if value and value.tenant_id == tenant_id and value.room_id == room_id else None

    def revoke_scope(
        self, *, tenant_id: str, room_id: str, now: float,
        membership_digest: str | None = None, before_membership_epoch: int | None = None,
        minimum_fencing_token: int,
    ) -> int:
        if minimum_fencing_token < 1:
            raise SfuVendorIdentityError("sfu_vendor_fencing_invalid")
        revoked: set[str] = set()
        with self._store.lock:
            for handle, value in tuple(self._store.identities.items()):
                if value.tenant_id != tenant_id or value.room_id != room_id or value.status != "active":
                    continue
                if membership_digest is not None and value.membership_digest != membership_digest:
                    continue
                if before_membership_epoch is not None and value.membership_epoch >= before_membership_epoch:
                    continue
                fence = max(minimum_fencing_token, value.fencing_token + 1)
                self._store.identities[handle] = replace(
                    value, sealed_membership=None, status="revoked", revoked_at=now,
                    fencing_token=fence, version=value.version + 1,
                )
                revoked.add(handle)
            for handle, value in tuple(self._store.destinations.items()):
                if value.identity_handle in revoked and value.status == "active":
                    self._store.destinations[handle] = replace(
                        value, status="revoked", revoked_at=now,
                        fencing_token=max(minimum_fencing_token, value.fencing_token + 1),
                        version=value.version + 1,
                    )
        return len(revoked)

    def purge_expired(self, *, now: float, limit: int) -> int:
        _limit(limit)
        count = 0
        with self._store.lock:
            rows = sorted(
                (
                    value
                    for value in self._store.identities.values()
                    if value.expires_at <= now and value.status != "tombstoned"
                ),
                key=lambda value: (value.expires_at, value.identity_handle),
            )[:limit]
            for value in rows:
                self._store.identities[value.identity_handle] = replace(
                    value, sealed_membership=None, status="tombstoned", revoked_at=now,
                    fencing_token=value.fencing_token + 1, version=value.version + 1,
                )
                for handle, destination in tuple(self._store.destinations.items()):
                    if destination.identity_handle == value.identity_handle:
                        self._store.destinations[handle] = replace(
                            destination, status="tombstoned", revoked_at=now,
                            fencing_token=destination.fencing_token + 1,
                            version=destination.version + 1,
                        )
                count += 1
        return count


class SqlSfuVendorIdentityRepository:
    def __init__(self, *, db_engine=default_engine) -> None:
        self._engine = db_engine

    def active_for_membership(
        self, *, tenant_id: str, room_id: str, membership_digest: str,
        membership_epoch: int, identity_epoch: int,
        membership_digest_candidates: tuple[str, ...] = (),
        membership_digest_key_id: str | None = None,
    ) -> SfuVendorIdentityBinding | None:
        try:
            with Session(self._engine) as db:
                row = db.exec(select(SfuBroadcastVendorIdentityDB).where(
                    SfuBroadcastVendorIdentityDB.tenant_id == tenant_id,
                    SfuBroadcastVendorIdentityDB.room_id == room_id,
                    SfuBroadcastVendorIdentityDB.membership_digest.in_(
                        tuple({membership_digest, *membership_digest_candidates})
                    ),
                    SfuBroadcastVendorIdentityDB.membership_epoch == membership_epoch,
                    SfuBroadcastVendorIdentityDB.identity_epoch == identity_epoch,
                    SfuBroadcastVendorIdentityDB.status == "active",
                ).with_for_update()).first()
                if row is not None and (
                    row.membership_digest != membership_digest
                    or row.membership_digest_key_id != membership_digest_key_id
                ):
                    row.membership_digest = membership_digest
                    row.membership_digest_key_id = membership_digest_key_id
                    row.version += 1
                    row.updated_at = time.time()
                    db.add(row)
                    db.commit()
                    db.refresh(row)
                return _identity_from_row(row) if row else None
        except SQLAlchemyError as exc:
            raise SfuVendorIdentityError("sfu_vendor_identity_store_unavailable", 503) from exc

    def get_identity(self, *, tenant_id: str, room_id: str, identity_handle: str) -> SfuVendorIdentityBinding | None:
        try:
            with Session(self._engine) as db:
                row = db.exec(select(SfuBroadcastVendorIdentityDB).where(
                    SfuBroadcastVendorIdentityDB.id == identity_handle,
                    SfuBroadcastVendorIdentityDB.tenant_id == tenant_id,
                    SfuBroadcastVendorIdentityDB.room_id == room_id,
                )).first()
                return _identity_from_row(row) if row else None
        except SQLAlchemyError as exc:
            raise SfuVendorIdentityError("sfu_vendor_identity_store_unavailable", 503) from exc

    def save_identity(
        self, binding: SfuVendorIdentityBinding, *, expected_version: int,
    ) -> SfuVendorIdentityMutationResult:
        _validate_identity(binding)
        if expected_version != 0:
            return _result("conflict", reason="sfu_vendor_identity_version_conflict")
        try:
            with Session(self._engine) as db:
                replay = db.exec(select(SfuBroadcastVendorIdentityDB).where(
                    SfuBroadcastVendorIdentityDB.tenant_id == binding.tenant_id,
                    SfuBroadcastVendorIdentityDB.room_id == binding.room_id,
                    SfuBroadcastVendorIdentityDB.membership_digest == binding.membership_digest,
                    SfuBroadcastVendorIdentityDB.membership_epoch == binding.membership_epoch,
                    SfuBroadcastVendorIdentityDB.identity_epoch == binding.identity_epoch,
                    SfuBroadcastVendorIdentityDB.status == "active",
                )).first()
                if replay is not None:
                    return _result("saved", identity=_identity_from_row(replay), replayed=True)
                db.add(_identity_row(binding))
                db.commit()
                return _result("saved", identity=binding)
        except IntegrityError:
            return _result("conflict", reason="sfu_vendor_identity_handle_collision")
        except SQLAlchemyError as exc:
            raise SfuVendorIdentityError("sfu_vendor_identity_store_unavailable", 503) from exc

    def save_destination(
        self, binding: SfuVendorDestinationBinding,
    ) -> SfuVendorIdentityMutationResult:
        _validate_destination(binding)
        try:
            with Session(self._engine) as db:
                identity = db.get(SfuBroadcastVendorIdentityDB, binding.identity_handle)
                if identity is None or identity.status != "active":
                    return _result("not_found", reason="sfu_vendor_identity_stale")
                replay = db.exec(select(SfuBroadcastDestinationHandleDB).where(
                    SfuBroadcastDestinationHandleDB.identity_id == binding.identity_handle,
                    SfuBroadcastDestinationHandleDB.route_digest == binding.route_digest,
                    SfuBroadcastDestinationHandleDB.publication_digest == binding.publication_digest,
                    SfuBroadcastDestinationHandleDB.audience_digest == binding.audience_digest,
                    SfuBroadcastDestinationHandleDB.route_epoch == binding.route_epoch,
                    SfuBroadcastDestinationHandleDB.key_epoch == binding.key_epoch,
                    SfuBroadcastDestinationHandleDB.status == "active",
                )).first()
                if replay is not None:
                    return _result("saved", destination=_destination_from_row(replay), replayed=True)
                db.add(_destination_row(binding))
                db.commit()
                return _result("saved", destination=binding)
        except IntegrityError:
            return _result("conflict", reason="sfu_destination_handle_collision")
        except SQLAlchemyError as exc:
            raise SfuVendorIdentityError("sfu_vendor_identity_store_unavailable", 503) from exc

    def get_destination(
        self, *, tenant_id: str, room_id: str, destination_handle: str,
    ) -> SfuVendorDestinationBinding | None:
        try:
            with Session(self._engine) as db:
                row = db.exec(select(SfuBroadcastDestinationHandleDB).where(
                    SfuBroadcastDestinationHandleDB.id == destination_handle,
                    SfuBroadcastDestinationHandleDB.tenant_id == tenant_id,
                    SfuBroadcastDestinationHandleDB.room_id == room_id,
                )).first()
                return _destination_from_row(row) if row else None
        except SQLAlchemyError as exc:
            raise SfuVendorIdentityError("sfu_vendor_identity_store_unavailable", 503) from exc

    def revoke_scope(
        self, *, tenant_id: str, room_id: str, now: float,
        membership_digest: str | None = None, before_membership_epoch: int | None = None,
        minimum_fencing_token: int,
    ) -> int:
        if minimum_fencing_token < 1:
            raise SfuVendorIdentityError("sfu_vendor_fencing_invalid")
        try:
            with Session(self._engine) as db:
                query = select(SfuBroadcastVendorIdentityDB).where(
                    SfuBroadcastVendorIdentityDB.tenant_id == tenant_id,
                    SfuBroadcastVendorIdentityDB.room_id == room_id,
                    SfuBroadcastVendorIdentityDB.status == "active",
                )
                if membership_digest is not None:
                    query = query.where(SfuBroadcastVendorIdentityDB.membership_digest == membership_digest)
                if before_membership_epoch is not None:
                    query = query.where(SfuBroadcastVendorIdentityDB.membership_epoch < before_membership_epoch)
                rows = db.exec(query).all()
                ids = [row.id for row in rows]
                for row in rows:
                    row.membership_ciphertext = None
                    row.membership_nonce = None
                    row.wrapping_key_id = None
                    row.status = "revoked"
                    row.revoked_at = now
                    row.fencing_token = max(minimum_fencing_token, row.fencing_token + 1)
                    row.version += 1
                    row.updated_at = now
                    db.add(row)
                if ids:
                    destinations = db.exec(select(SfuBroadcastDestinationHandleDB).where(
                        SfuBroadcastDestinationHandleDB.identity_id.in_(ids),
                        SfuBroadcastDestinationHandleDB.status == "active",
                    )).all()
                    for destination in destinations:
                        destination.status = "revoked"
                        destination.revoked_at = now
                        destination.fencing_token = max(
                            minimum_fencing_token, destination.fencing_token + 1
                        )
                        destination.version += 1
                        destination.updated_at = now
                        db.add(destination)
                db.commit()
                return len(rows)
        except SQLAlchemyError as exc:
            raise SfuVendorIdentityError("sfu_vendor_identity_store_unavailable", 503) from exc

    def purge_expired(self, *, now: float, limit: int) -> int:
        _limit(limit)
        try:
            with Session(self._engine) as db:
                rows = db.exec(select(SfuBroadcastVendorIdentityDB).where(
                    SfuBroadcastVendorIdentityDB.expires_at <= now,
                    SfuBroadcastVendorIdentityDB.status != "tombstoned",
                ).order_by(
                    SfuBroadcastVendorIdentityDB.expires_at,
                    SfuBroadcastVendorIdentityDB.id,
                ).limit(limit)).all()
                ids = [row.id for row in rows]
                for row in rows:
                    row.membership_ciphertext = None
                    row.membership_nonce = None
                    row.wrapping_key_id = None
                    row.status = "tombstoned"
                    row.revoked_at = now
                    row.fencing_token += 1
                    row.version += 1
                    row.updated_at = now
                    db.add(row)
                if ids:
                    db.exec(sa.update(SfuBroadcastDestinationHandleDB).where(
                        SfuBroadcastDestinationHandleDB.identity_id.in_(ids)
                    ).values(
                        status="tombstoned", revoked_at=now,
                        fencing_token=SfuBroadcastDestinationHandleDB.fencing_token + 1,
                        version=SfuBroadcastDestinationHandleDB.version + 1,
                        updated_at=now,
                    ))
                db.commit()
                return len(rows)
        except SQLAlchemyError as exc:
            raise SfuVendorIdentityError("sfu_vendor_identity_store_unavailable", 503) from exc


def _identity_row(value: SfuVendorIdentityBinding) -> SfuBroadcastVendorIdentityDB:
    sealed = value.sealed_membership
    if sealed is None:
        raise SfuVendorIdentityError("sfu_vendor_identity_binding_invalid")
    return SfuBroadcastVendorIdentityDB(
        id=value.identity_handle, tenant_id=value.tenant_id, room_id=value.room_id,
        membership_digest=value.membership_digest,
        membership_digest_key_id=value.membership_digest_key_id,
        membership_ciphertext=sealed.ciphertext, membership_nonce=sealed.nonce,
        wrapping_key_id=sealed.key_id, membership_epoch=value.membership_epoch,
        identity_epoch=value.identity_epoch, status=value.status,
        fencing_token=value.fencing_token, version=value.version,
        issued_at=value.issued_at, expires_at=value.expires_at,
        revoked_at=value.revoked_at, created_at=value.issued_at, updated_at=value.issued_at,
    )


def _identity_from_row(row: SfuBroadcastVendorIdentityDB) -> SfuVendorIdentityBinding:
    sealed = (
        SfuHubSealedSecret(row.wrapping_key_id, row.membership_nonce, row.membership_ciphertext)
        if row.wrapping_key_id and row.membership_nonce is not None and row.membership_ciphertext is not None
        else None
    )
    return SfuVendorIdentityBinding(
        row.id, row.tenant_id, row.room_id, row.membership_digest, sealed,
        row.membership_epoch, row.identity_epoch, row.status, row.fencing_token,
        row.version, row.issued_at, row.expires_at, row.revoked_at,
        row.membership_digest_key_id,
    )


def _destination_row(value: SfuVendorDestinationBinding) -> SfuBroadcastDestinationHandleDB:
    return SfuBroadcastDestinationHandleDB(
        id=value.destination_handle, identity_id=value.identity_handle,
        tenant_id=value.tenant_id, room_id=value.room_id,
        route_digest=value.route_digest, publication_digest=value.publication_digest,
        audience_digest=value.audience_digest, membership_epoch=value.membership_epoch,
        identity_epoch=value.identity_epoch, route_epoch=value.route_epoch,
        key_epoch=value.key_epoch, status=value.status, fencing_token=value.fencing_token,
        version=value.version, issued_at=value.issued_at, expires_at=value.expires_at,
        revoked_at=value.revoked_at, created_at=value.issued_at, updated_at=value.issued_at,
    )


def _destination_from_row(row: SfuBroadcastDestinationHandleDB) -> SfuVendorDestinationBinding:
    return SfuVendorDestinationBinding(
        row.id, row.identity_id, row.tenant_id, row.room_id,
        row.route_digest, row.publication_digest, row.audience_digest,
        row.membership_epoch, row.identity_epoch, row.route_epoch, row.key_epoch,
        row.status, row.fencing_token, row.version, row.issued_at, row.expires_at, row.revoked_at,
    )


def _validate_identity(value: SfuVendorIdentityBinding) -> None:
    if (
        value.status != "active" or value.sealed_membership is None
        or value.membership_epoch < 1 or value.identity_epoch < 1
        or value.fencing_token < 1 or value.version != 1
        or value.expires_at <= value.issued_at
    ):
        raise SfuVendorIdentityError("sfu_vendor_identity_binding_invalid")


def _validate_destination(value: SfuVendorDestinationBinding) -> None:
    if (
        value.status != "active" or value.version != 1 or value.fencing_token < 1
        or value.expires_at <= value.issued_at
        or any(epoch < 1 for epoch in (
            value.membership_epoch, value.identity_epoch, value.route_epoch, value.key_epoch
        ))
    ):
        raise SfuVendorIdentityError("sfu_destination_binding_invalid")


def _limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise SfuVendorIdentityError("sfu_vendor_page_limit_invalid")


def _result(
    status: str, *, identity: SfuVendorIdentityBinding | None = None,
    destination: SfuVendorDestinationBinding | None = None,
    replayed: bool = False, reason: str | None = None,
) -> SfuVendorIdentityMutationResult:
    return SfuVendorIdentityMutationResult(status, identity, destination, replayed, reason)


__all__ = [
    "InMemorySfuVendorIdentityRepository",
    "InMemorySfuVendorIdentityStore",
    "SqlSfuVendorIdentityRepository",
]
