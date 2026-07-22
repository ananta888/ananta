"""Durable CAS boundary for Hub-owned SFU runtime identities."""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import (
    SfuRuntimeCredentialDB,
    SfuRuntimeEnrollmentRateLimitDB,
    SfuRuntimeIdentityDB,
    SfuRuntimeIdentityMutationDB,
)


class SfuRuntimeIdentityRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuRuntimeCredentialRegistration:
    credential_kind: str
    public_key_fingerprint: str
    credential_fingerprint: str
    proof_nonce_digest: str
    certificate_serial: str | None = None
    certificate_sans: tuple[str, ...] = ()
    certificate_ekus: tuple[str, ...] = ()
    certificate_not_before: float | None = None
    certificate_not_after: float | None = None


@dataclass(frozen=True, slots=True)
class SfuRuntimeCredentialRecord:
    id: str
    credential_kind: str
    public_key_fingerprint: str
    credential_fingerprint: str
    certificate_serial: str | None
    certificate_sans: tuple[str, ...]
    certificate_ekus: tuple[str, ...]
    certificate_not_before: float | None
    certificate_not_after: float | None
    status: str
    valid_from: float
    overlap_until: float | None
    revoked_at: float | None

    def usable_at(self, now: float) -> bool:
        return self.status == "active" or (
            self.status == "overlap"
            and self.overlap_until is not None
            and now <= self.overlap_until
        )

    def payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "credential_kind": self.credential_kind,
            "public_key_fingerprint": self.public_key_fingerprint,
            "credential_fingerprint": self.credential_fingerprint,
            "certificate_serial": self.certificate_serial,
            "certificate_sans": list(self.certificate_sans),
            "certificate_ekus": list(self.certificate_ekus),
            "certificate_not_before": self.certificate_not_before,
            "certificate_not_after": self.certificate_not_after,
            "status": self.status,
            "valid_from": self.valid_from,
            "overlap_until": self.overlap_until,
            "revoked_at": self.revoked_at,
        }


@dataclass(frozen=True, slots=True)
class SfuRuntimeIdentityRecord:
    id: str
    node_id: str
    runtime_control_mode: str
    roles: tuple[str, ...]
    status: str
    version: int
    active_credential_id: str
    previous_credential_id: str | None
    actor: str
    reason: str
    enrolled_at: float
    rotated_at: float | None
    revoked_at: float | None
    revocation_deadline_at: float | None
    credentials: tuple[SfuRuntimeCredentialRecord, ...]

    def payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "runtime_control_mode": self.runtime_control_mode,
            "roles": list(self.roles),
            "status": self.status,
            "version": self.version,
            "active_credential_id": self.active_credential_id,
            "previous_credential_id": self.previous_credential_id,
            "actor": self.actor,
            "reason": self.reason,
            "enrolled_at": self.enrolled_at,
            "rotated_at": self.rotated_at,
            "revoked_at": self.revoked_at,
            "revocation_deadline_at": self.revocation_deadline_at,
            "credentials": [item.payload() for item in self.credentials],
        }


@dataclass(frozen=True, slots=True)
class SfuRuntimeIdentityMutationResult:
    status: str
    identity: SfuRuntimeIdentityRecord


@dataclass(frozen=True, slots=True)
class SfuEnrollmentRateDecision:
    allowed: bool
    attempts: int
    limit: int
    window_started_at: int


class SfuRuntimeIdentityRepositoryPort(Protocol):
    def consume_enrollment_attempt(
        self, *, actor: str, source: str, window_seconds: int, limit: int
    ) -> SfuEnrollmentRateDecision: ...

    def create_identity(
        self,
        *,
        node_id: str,
        runtime_control_mode: str,
        roles: tuple[str, ...],
        credential: SfuRuntimeCredentialRegistration,
        expected_version: int,
        actor: str,
        reason: str,
        idempotency_key: str,
        request_digest: str,
    ) -> SfuRuntimeIdentityMutationResult: ...

    def rotate_identity(
        self,
        *,
        node_id: str,
        credential: SfuRuntimeCredentialRegistration,
        expected_version: int,
        overlap_seconds: int,
        actor: str,
        reason: str,
        idempotency_key: str,
        request_digest: str,
    ) -> SfuRuntimeIdentityMutationResult: ...

    def revoke_identity(
        self,
        *,
        node_id: str,
        expected_version: int,
        revocation_max_seconds: int,
        emergency: bool,
        actor: str,
        reason: str,
        idempotency_key: str,
        request_digest: str,
    ) -> SfuRuntimeIdentityMutationResult: ...

    def get_by_node_id(self, node_id: str) -> SfuRuntimeIdentityRecord | None: ...


class SqlSfuRuntimeIdentityRepository:
    """SQL-only authority; no process-local identity or revocation cache exists."""

    def __init__(self, *, db_engine=default_engine, clock=time.time) -> None:
        self._engine = db_engine
        self._clock = clock

    def consume_enrollment_attempt(
        self, *, actor: str, source: str, window_seconds: int, limit: int
    ) -> SfuEnrollmentRateDecision:
        now = float(self._clock())
        window = int(now // window_seconds) * window_seconds
        source_digest = _digest(source or "unknown")
        bucket_id = _digest(f"{actor}\0{source_digest}\0{window}")
        for _ in range(3):
            try:
                with Session(self._engine) as db:
                    row = db.get(SfuRuntimeEnrollmentRateLimitDB, bucket_id)
                    if row is None:
                        row = SfuRuntimeEnrollmentRateLimitDB(
                            id=bucket_id,
                            actor=actor,
                            source_digest=source_digest,
                            window_started_at=window,
                            attempts=1,
                            version=1,
                            updated_at=now,
                        )
                        db.add(row)
                        db.commit()
                        return SfuEnrollmentRateDecision(True, 1, limit, window)
                    next_attempt = row.attempts + 1
                    result = db.exec(
                        sa.update(SfuRuntimeEnrollmentRateLimitDB)
                        .where(
                            SfuRuntimeEnrollmentRateLimitDB.id == bucket_id,
                            SfuRuntimeEnrollmentRateLimitDB.version == row.version,
                        )
                        .values(attempts=next_attempt, version=row.version + 1, updated_at=now)
                    )
                    if result.rowcount != 1:
                        db.rollback()
                        continue
                    db.commit()
                    return SfuEnrollmentRateDecision(next_attempt <= limit, next_attempt, limit, window)
            except IntegrityError:
                continue
            except SQLAlchemyError as exc:
                raise SfuRuntimeIdentityRepositoryError("sfu_identity_store_unavailable") from exc
        raise SfuRuntimeIdentityRepositoryError("sfu_enrollment_rate_limit_conflict")

    def create_identity(
        self,
        *,
        node_id: str,
        runtime_control_mode: str,
        roles: tuple[str, ...],
        credential: SfuRuntimeCredentialRegistration,
        expected_version: int,
        actor: str,
        reason: str,
        idempotency_key: str,
        request_digest: str,
    ) -> SfuRuntimeIdentityMutationResult:
        if expected_version != 0:
            raise SfuRuntimeIdentityRepositoryError("sfu_identity_create_expected_version_invalid")
        operation = "enroll"
        replay = self._load_replay(actor, idempotency_key, request_digest)
        if replay is not None:
            return replay
        now = float(self._clock())
        identity_id = f"sfu-runtime-{uuid.uuid4().hex}"
        credential_id = f"sfu-credential-{uuid.uuid4().hex}"
        try:
            with Session(self._engine) as db:
                identity_row = SfuRuntimeIdentityDB(
                    id=identity_id,
                    node_id=node_id,
                    runtime_control_mode=runtime_control_mode,
                    roles=list(roles),
                    status="active",
                    version=1,
                    active_credential_id=credential_id,
                    actor=actor,
                    reason=reason,
                    enrolled_at=now,
                    created_at=now,
                    updated_at=now,
                )
                credential_row = _new_credential_row(
                    identity_id=identity_id,
                    credential_id=credential_id,
                    credential=credential,
                    now=now,
                )
                record = _record_from_rows(identity_row, (credential_row,))
                db.add(identity_row)
                db.add(credential_row)
                db.add(
                    _mutation_row(
                        record=record,
                        operation=operation,
                        expected_version=expected_version,
                        status="created",
                        actor=actor,
                        reason=reason,
                        idempotency_key=idempotency_key,
                        request_digest=request_digest,
                        now=now,
                    )
                )
                db.commit()
                return SfuRuntimeIdentityMutationResult("created", record)
        except IntegrityError as exc:
            replay = self._load_replay(actor, idempotency_key, request_digest)
            if replay is not None:
                return replay
            raise SfuRuntimeIdentityRepositoryError("sfu_identity_or_credential_conflict") from exc
        except SQLAlchemyError as exc:
            raise SfuRuntimeIdentityRepositoryError("sfu_identity_store_unavailable") from exc

    def rotate_identity(
        self,
        *,
        node_id: str,
        credential: SfuRuntimeCredentialRegistration,
        expected_version: int,
        overlap_seconds: int,
        actor: str,
        reason: str,
        idempotency_key: str,
        request_digest: str,
    ) -> SfuRuntimeIdentityMutationResult:
        operation = "rotate"
        replay = self._load_replay(actor, idempotency_key, request_digest)
        if replay is not None:
            return replay
        now = float(self._clock())
        try:
            with Session(self._engine) as db:
                current = db.exec(
                    select(SfuRuntimeIdentityDB).where(SfuRuntimeIdentityDB.node_id == node_id)
                ).first()
                if current is None:
                    raise SfuRuntimeIdentityRepositoryError("sfu_identity_not_found")
                if current.status == "revoked":
                    raise SfuRuntimeIdentityRepositoryError("sfu_identity_revoked")
                if current.version != expected_version:
                    raise SfuRuntimeIdentityRepositoryError("sfu_identity_version_conflict")
                previous_credential_id = current.active_credential_id
                new_credential_id = f"sfu-credential-{uuid.uuid4().hex}"
                updated = db.exec(
                    sa.update(SfuRuntimeIdentityDB)
                    .where(
                        SfuRuntimeIdentityDB.id == current.id,
                        SfuRuntimeIdentityDB.version == expected_version,
                        SfuRuntimeIdentityDB.status != "revoked",
                    )
                    .values(
                        version=expected_version + 1,
                        active_credential_id=new_credential_id,
                        previous_credential_id=previous_credential_id,
                        actor=actor,
                        reason=reason,
                        rotated_at=now,
                        updated_at=now,
                    )
                )
                if updated.rowcount != 1:
                    db.rollback()
                    raise SfuRuntimeIdentityRepositoryError("sfu_identity_version_conflict")
                db.exec(
                    sa.update(SfuRuntimeCredentialDB)
                    .where(
                        SfuRuntimeCredentialDB.identity_id == current.id,
                        SfuRuntimeCredentialDB.status == "overlap",
                    )
                    .values(status="revoked", revoked_at=now)
                )
                prior = db.exec(
                    sa.update(SfuRuntimeCredentialDB)
                    .where(
                        SfuRuntimeCredentialDB.id == previous_credential_id,
                        SfuRuntimeCredentialDB.identity_id == current.id,
                        SfuRuntimeCredentialDB.status == "active",
                    )
                    .values(status="overlap", overlap_until=now + overlap_seconds)
                )
                if prior.rowcount != 1:
                    db.rollback()
                    raise SfuRuntimeIdentityRepositoryError("sfu_active_credential_missing")
                db.add(
                    _new_credential_row(
                        identity_id=current.id,
                        credential_id=new_credential_id,
                        credential=credential,
                        now=now,
                    )
                )
                db.flush()
                db.expire_all()
                identity_row = db.get(SfuRuntimeIdentityDB, current.id)
                if identity_row is None:
                    raise SfuRuntimeIdentityRepositoryError("sfu_identity_not_found")
                record = _record_for_identity(db, identity_row)
                db.add(
                    _mutation_row(
                        record=record,
                        operation=operation,
                        expected_version=expected_version,
                        status="updated",
                        actor=actor,
                        reason=reason,
                        idempotency_key=idempotency_key,
                        request_digest=request_digest,
                        now=now,
                    )
                )
                db.commit()
                return SfuRuntimeIdentityMutationResult("updated", record)
        except SfuRuntimeIdentityRepositoryError:
            raise
        except IntegrityError as exc:
            replay = self._load_replay(actor, idempotency_key, request_digest)
            if replay is not None:
                return replay
            raise SfuRuntimeIdentityRepositoryError("sfu_identity_or_credential_conflict") from exc
        except SQLAlchemyError as exc:
            raise SfuRuntimeIdentityRepositoryError("sfu_identity_store_unavailable") from exc

    def revoke_identity(
        self,
        *,
        node_id: str,
        expected_version: int,
        revocation_max_seconds: int,
        emergency: bool,
        actor: str,
        reason: str,
        idempotency_key: str,
        request_digest: str,
    ) -> SfuRuntimeIdentityMutationResult:
        operation = "emergency_revoke" if emergency else "revoke"
        replay = self._load_replay(actor, idempotency_key, request_digest)
        if replay is not None:
            return replay
        now = float(self._clock())
        try:
            with Session(self._engine) as db:
                current = db.exec(
                    select(SfuRuntimeIdentityDB).where(SfuRuntimeIdentityDB.node_id == node_id)
                ).first()
                if current is None:
                    raise SfuRuntimeIdentityRepositoryError("sfu_identity_not_found")
                if current.version != expected_version:
                    raise SfuRuntimeIdentityRepositoryError("sfu_identity_version_conflict")
                updated = db.exec(
                    sa.update(SfuRuntimeIdentityDB)
                    .where(
                        SfuRuntimeIdentityDB.id == current.id,
                        SfuRuntimeIdentityDB.version == expected_version,
                    )
                    .values(
                        status="revoked",
                        version=expected_version + 1,
                        actor=actor,
                        reason=reason,
                        revoked_at=now,
                        revocation_deadline_at=now + revocation_max_seconds,
                        updated_at=now,
                    )
                )
                if updated.rowcount != 1:
                    db.rollback()
                    raise SfuRuntimeIdentityRepositoryError("sfu_identity_version_conflict")
                db.exec(
                    sa.update(SfuRuntimeCredentialDB)
                    .where(SfuRuntimeCredentialDB.identity_id == current.id)
                    .values(status="revoked", overlap_until=None, revoked_at=now)
                )
                db.flush()
                db.expire_all()
                identity_row = db.get(SfuRuntimeIdentityDB, current.id)
                if identity_row is None:
                    raise SfuRuntimeIdentityRepositoryError("sfu_identity_not_found")
                record = _record_for_identity(db, identity_row)
                db.add(
                    _mutation_row(
                        record=record,
                        operation=operation,
                        expected_version=expected_version,
                        status="revoked",
                        actor=actor,
                        reason=reason,
                        idempotency_key=idempotency_key,
                        request_digest=request_digest,
                        now=now,
                    )
                )
                db.commit()
                return SfuRuntimeIdentityMutationResult("revoked", record)
        except SfuRuntimeIdentityRepositoryError:
            raise
        except IntegrityError as exc:
            replay = self._load_replay(actor, idempotency_key, request_digest)
            if replay is not None:
                return replay
            raise SfuRuntimeIdentityRepositoryError("sfu_identity_mutation_conflict") from exc
        except SQLAlchemyError as exc:
            raise SfuRuntimeIdentityRepositoryError("sfu_identity_store_unavailable") from exc

    def get_by_node_id(self, node_id: str) -> SfuRuntimeIdentityRecord | None:
        try:
            with Session(self._engine) as db:
                row = db.exec(
                    select(SfuRuntimeIdentityDB).where(SfuRuntimeIdentityDB.node_id == node_id)
                ).first()
                return _record_for_identity(db, row) if row is not None else None
        except SQLAlchemyError as exc:
            raise SfuRuntimeIdentityRepositoryError("sfu_identity_store_unavailable") from exc

    def _load_replay(
        self, actor: str, idempotency_key: str, request_digest: str
    ) -> SfuRuntimeIdentityMutationResult | None:
        key_digest = _digest(idempotency_key)
        try:
            with Session(self._engine) as db:
                receipt = db.exec(
                    select(SfuRuntimeIdentityMutationDB).where(
                        SfuRuntimeIdentityMutationDB.actor == actor,
                        SfuRuntimeIdentityMutationDB.idempotency_key_digest == key_digest,
                    )
                ).first()
                if receipt is None:
                    return None
                if receipt.request_digest != request_digest:
                    raise SfuRuntimeIdentityRepositoryError("sfu_identity_idempotency_conflict")
                return SfuRuntimeIdentityMutationResult(
                    "replayed", _record_from_payload(receipt.response_json)
                )
        except SfuRuntimeIdentityRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise SfuRuntimeIdentityRepositoryError("sfu_identity_store_unavailable") from exc


def _new_credential_row(
    *,
    identity_id: str,
    credential_id: str,
    credential: SfuRuntimeCredentialRegistration,
    now: float,
) -> SfuRuntimeCredentialDB:
    return SfuRuntimeCredentialDB(
        id=credential_id,
        identity_id=identity_id,
        credential_kind=credential.credential_kind,
        public_key_fingerprint=credential.public_key_fingerprint,
        credential_fingerprint=credential.credential_fingerprint,
        proof_nonce_digest=credential.proof_nonce_digest,
        certificate_serial=credential.certificate_serial,
        certificate_sans=list(credential.certificate_sans),
        certificate_ekus=list(credential.certificate_ekus),
        certificate_not_before=credential.certificate_not_before,
        certificate_not_after=credential.certificate_not_after,
        status="active",
        valid_from=now,
        created_at=now,
    )


def _mutation_row(
    *,
    record: SfuRuntimeIdentityRecord,
    operation: str,
    expected_version: int,
    status: str,
    actor: str,
    reason: str,
    idempotency_key: str,
    request_digest: str,
    now: float,
) -> SfuRuntimeIdentityMutationDB:
    return SfuRuntimeIdentityMutationDB(
        identity_id=record.id,
        node_id=record.node_id,
        operation=operation,
        expected_version=expected_version,
        result_version=record.version,
        result_status=status,
        actor=actor,
        reason=reason,
        idempotency_key_digest=_digest(idempotency_key),
        request_digest=request_digest,
        response_json=record.payload(),
        audited_at=now,
    )


def _record_for_identity(db: Session, row: SfuRuntimeIdentityDB) -> SfuRuntimeIdentityRecord:
    credentials = db.exec(
        select(SfuRuntimeCredentialDB)
        .where(SfuRuntimeCredentialDB.identity_id == row.id)
        .order_by(SfuRuntimeCredentialDB.created_at, SfuRuntimeCredentialDB.id)
    ).all()
    return _record_from_rows(row, tuple(credentials))


def _record_from_rows(
    identity: SfuRuntimeIdentityDB,
    credentials: tuple[SfuRuntimeCredentialDB, ...],
) -> SfuRuntimeIdentityRecord:
    return SfuRuntimeIdentityRecord(
        id=identity.id,
        node_id=identity.node_id,
        runtime_control_mode=identity.runtime_control_mode,
        roles=tuple(str(role) for role in identity.roles),
        status=identity.status,
        version=identity.version,
        active_credential_id=identity.active_credential_id,
        previous_credential_id=identity.previous_credential_id,
        actor=identity.actor,
        reason=identity.reason,
        enrolled_at=identity.enrolled_at,
        rotated_at=identity.rotated_at,
        revoked_at=identity.revoked_at,
        revocation_deadline_at=identity.revocation_deadline_at,
        credentials=tuple(
            SfuRuntimeCredentialRecord(
                id=item.id,
                credential_kind=item.credential_kind,
                public_key_fingerprint=item.public_key_fingerprint,
                credential_fingerprint=item.credential_fingerprint,
                certificate_serial=item.certificate_serial,
                certificate_sans=tuple(item.certificate_sans),
                certificate_ekus=tuple(item.certificate_ekus),
                certificate_not_before=item.certificate_not_before,
                certificate_not_after=item.certificate_not_after,
                status=item.status,
                valid_from=item.valid_from,
                overlap_until=item.overlap_until,
                revoked_at=item.revoked_at,
            )
            for item in credentials
        ),
    )


def _record_from_payload(payload: dict) -> SfuRuntimeIdentityRecord:
    credentials = tuple(
        SfuRuntimeCredentialRecord(
            id=str(item["id"]),
            credential_kind=str(item["credential_kind"]),
            public_key_fingerprint=str(item["public_key_fingerprint"]),
            credential_fingerprint=str(item["credential_fingerprint"]),
            certificate_serial=item.get("certificate_serial"),
            certificate_sans=tuple(item.get("certificate_sans") or ()),
            certificate_ekus=tuple(item.get("certificate_ekus") or ()),
            certificate_not_before=item.get("certificate_not_before"),
            certificate_not_after=item.get("certificate_not_after"),
            status=str(item["status"]),
            valid_from=float(item["valid_from"]),
            overlap_until=item.get("overlap_until"),
            revoked_at=item.get("revoked_at"),
        )
        for item in payload.get("credentials", ())
    )
    return SfuRuntimeIdentityRecord(
        id=str(payload["id"]),
        node_id=str(payload["node_id"]),
        runtime_control_mode=str(payload["runtime_control_mode"]),
        roles=tuple(payload.get("roles") or ()),
        status=str(payload["status"]),
        version=int(payload["version"]),
        active_credential_id=str(payload["active_credential_id"]),
        previous_credential_id=payload.get("previous_credential_id"),
        actor=str(payload["actor"]),
        reason=str(payload["reason"]),
        enrolled_at=float(payload["enrolled_at"]),
        rotated_at=payload.get("rotated_at"),
        revoked_at=payload.get("revoked_at"),
        revocation_deadline_at=payload.get("revocation_deadline_at"),
        credentials=credentials,
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "SfuEnrollmentRateDecision",
    "SfuRuntimeCredentialRecord",
    "SfuRuntimeCredentialRegistration",
    "SfuRuntimeIdentityMutationResult",
    "SfuRuntimeIdentityRecord",
    "SfuRuntimeIdentityRepositoryError",
    "SfuRuntimeIdentityRepositoryPort",
    "SqlSfuRuntimeIdentityRepository",
]
