"""Persistent TURN observer identity repository with CAS and receipts."""

from __future__ import annotations

import hashlib
import time

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.turn_observer_identities import (
    TurnObserverCredentialDB,
    TurnObserverEnrollmentRateLimitDB,
    TurnObserverIdentityDB,
    TurnObserverIdentityMutationDB,
)


class TurnObserverIdentityRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SqlTurnObserverIdentityRepository:
    """Always reads durable state; there is intentionally no in-memory fallback."""

    def __init__(
        self,
        *,
        db_engine,
        clock=time.time,
        mutation_retention_seconds: int = 86_400,
        purge_batch: int = 128,
    ) -> None:
        if not 60 <= mutation_retention_seconds <= 604_800 or not 1 <= purge_batch <= 1_000:
            raise ValueError("turn_observer_repository_limits_invalid")
        self._engine = db_engine
        self._clock = clock
        self._mutation_retention = mutation_retention_seconds
        self._purge_batch = purge_batch

    def get(self, *, pool_id: str, instance_id: str) -> TurnObserverIdentityDB | None:
        with Session(self._engine, expire_on_commit=False) as db:
            return db.exec(
                select(TurnObserverIdentityDB).where(
                    TurnObserverIdentityDB.pool_id == pool_id,
                    TurnObserverIdentityDB.instance_id == instance_id,
                )
            ).first()

    def get_by_id(self, identity_id: str) -> TurnObserverIdentityDB | None:
        with Session(self._engine, expire_on_commit=False) as db:
            return db.get(TurnObserverIdentityDB, identity_id)

    def credential(self, credential_id: str) -> TurnObserverCredentialDB | None:
        with Session(self._engine, expire_on_commit=False) as db:
            return db.get(TurnObserverCredentialDB, credential_id)

    def receipt(self, *, actor: str, key_digest: str) -> TurnObserverIdentityMutationDB | None:
        now = float(self._clock())
        with Session(self._engine, expire_on_commit=False) as db:
            return db.exec(
                select(TurnObserverIdentityMutationDB).where(
                    TurnObserverIdentityMutationDB.actor == actor,
                    TurnObserverIdentityMutationDB.idempotency_key_digest == key_digest,
                    TurnObserverIdentityMutationDB.expires_at.is_not(None),
                    TurnObserverIdentityMutationDB.expires_at > now,
                )
            ).first()

    def consume_rate_limit(
        self,
        *,
        actor: str,
        source_digest: str,
        now: float,
        window_seconds: int,
        attempts_max: int,
    ) -> None:
        window = int(now) // window_seconds * window_seconds
        row_id = hashlib.sha256(f"{actor}\0{source_digest}\0{window}".encode()).hexdigest()
        with Session(self._engine, expire_on_commit=False) as db:
            self._purge_expired(db, now)
            row = db.get(TurnObserverEnrollmentRateLimitDB, row_id)
            if row is None:
                row = TurnObserverEnrollmentRateLimitDB(
                    id=row_id,
                    actor=actor,
                    source_digest=source_digest,
                    window_started_at=window,
                    attempts=1,
                    updated_at=now,
                    expires_at=window + window_seconds,
                )
                db.add(row)
            elif row.attempts >= attempts_max:
                raise TurnObserverIdentityRepositoryError("turn_observer_enrollment_rate_exceeded")
            else:
                row.attempts += 1
                row.version += 1
                row.updated_at = now
                db.add(row)
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise TurnObserverIdentityRepositoryError("turn_observer_enrollment_rate_conflict") from exc

    def create(
        self,
        identity: TurnObserverIdentityDB,
        credential: TurnObserverCredentialDB,
        mutation: TurnObserverIdentityMutationDB,
    ) -> TurnObserverIdentityDB:
        self._prepare_mutation(mutation)
        with Session(self._engine, expire_on_commit=False) as db:
            self._purge_expired(db, float(self._clock()))
            self._delete_expired_receipt(db, mutation)
            db.add(identity)
            db.add(credential)
            db.add(mutation)
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise TurnObserverIdentityRepositoryError("turn_observer_identity_conflict") from exc
            return identity

    def rotate(
        self,
        *,
        identity: TurnObserverIdentityDB,
        credential: TurnObserverCredentialDB,
        expected_version: int,
        overlap_until: float,
        mutation: TurnObserverIdentityMutationDB,
        now: float,
    ) -> TurnObserverIdentityDB:
        self._prepare_mutation(mutation)
        new_version = expected_version + 1
        with Session(self._engine, expire_on_commit=False) as db:
            self._purge_expired(db, float(self._clock()))
            self._delete_expired_receipt(db, mutation)
            current = db.get(TurnObserverIdentityDB, identity.id)
            if current is None or current.status != "active" or current.version != expected_version:
                raise TurnObserverIdentityRepositoryError("turn_observer_version_conflict")
            old = db.get(TurnObserverCredentialDB, current.active_credential_id)
            if old is None:
                raise TurnObserverIdentityRepositoryError("turn_observer_credential_state_invalid")
            old.status = "overlap"
            old.overlap_until = overlap_until
            db.add(old)
            result = db.exec(
                update(TurnObserverIdentityDB)
                .where(
                    TurnObserverIdentityDB.id == identity.id,
                    TurnObserverIdentityDB.version == expected_version,
                    TurnObserverIdentityDB.status == "active",
                )
                .values(
                    active_credential_id=credential.id,
                    previous_credential_id=old.id,
                    rotation_overlap_until=overlap_until,
                    version=new_version,
                    rotated_at=now,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise TurnObserverIdentityRepositoryError("turn_observer_version_conflict")
            db.add(credential)
            db.add(mutation)
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise TurnObserverIdentityRepositoryError("turn_observer_identity_conflict") from exc
        updated = self.get_by_id(identity.id)
        if updated is None:
            raise TurnObserverIdentityRepositoryError("turn_observer_identity_unavailable")
        return updated

    def revoke(
        self,
        *,
        identity: TurnObserverIdentityDB,
        expected_version: int,
        mutation: TurnObserverIdentityMutationDB,
        now: float,
    ) -> TurnObserverIdentityDB:
        self._prepare_mutation(mutation)
        with Session(self._engine, expire_on_commit=False) as db:
            self._purge_expired(db, float(self._clock()))
            self._delete_expired_receipt(db, mutation)
            result = db.exec(
                update(TurnObserverIdentityDB)
                .where(
                    TurnObserverIdentityDB.id == identity.id,
                    TurnObserverIdentityDB.version == expected_version,
                    TurnObserverIdentityDB.status == "active",
                )
                .values(
                    status="revoked",
                    version=expected_version + 1,
                    revoked_at=now,
                    recovery_evidence_required=True,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise TurnObserverIdentityRepositoryError("turn_observer_version_conflict")
            credentials = db.exec(
                select(TurnObserverCredentialDB).where(TurnObserverCredentialDB.identity_id == identity.id)
            ).all()
            for credential in credentials:
                credential.status = "revoked"
                credential.revoked_at = now
                db.add(credential)
            db.add(mutation)
            db.commit()
        updated = self.get_by_id(identity.id)
        if updated is None:
            raise TurnObserverIdentityRepositoryError("turn_observer_identity_unavailable")
        return updated

    def purge_expired(self, *, now: float | None = None) -> int:
        effective_now = float(self._clock() if now is None else now)
        with Session(self._engine) as db:
            count = self._purge_expired(db, effective_now)
            db.commit()
            return count

    def _prepare_mutation(self, mutation: TurnObserverIdentityMutationDB) -> None:
        mutation.expires_at = float(mutation.audited_at) + self._mutation_retention

    @staticmethod
    def _delete_expired_receipt(
        db: Session, mutation: TurnObserverIdentityMutationDB
    ) -> None:
        db.exec(
            delete(TurnObserverIdentityMutationDB).where(
                TurnObserverIdentityMutationDB.actor == mutation.actor,
                TurnObserverIdentityMutationDB.idempotency_key_digest
                == mutation.idempotency_key_digest,
                TurnObserverIdentityMutationDB.expires_at.is_not(None),
                TurnObserverIdentityMutationDB.expires_at <= mutation.audited_at,
            )
        )

    def _purge_expired(self, db: Session, now: float) -> int:
        mutation_ids = list(
            db.exec(
                select(TurnObserverIdentityMutationDB.id)
                .where(
                    TurnObserverIdentityMutationDB.expires_at.is_not(None),
                    TurnObserverIdentityMutationDB.expires_at <= now,
                )
                .order_by(TurnObserverIdentityMutationDB.expires_at)
                .limit(self._purge_batch)
            ).all()
        )
        rate_ids = list(
            db.exec(
                select(TurnObserverEnrollmentRateLimitDB.id)
                .where(
                    TurnObserverEnrollmentRateLimitDB.expires_at.is_not(None),
                    TurnObserverEnrollmentRateLimitDB.expires_at <= now,
                )
                .order_by(TurnObserverEnrollmentRateLimitDB.expires_at)
                .limit(self._purge_batch)
            ).all()
        )
        if mutation_ids:
            db.exec(
                delete(TurnObserverIdentityMutationDB).where(
                    TurnObserverIdentityMutationDB.id.in_(mutation_ids)
                )
            )
        if rate_ids:
            db.exec(
                delete(TurnObserverEnrollmentRateLimitDB).where(
                    TurnObserverEnrollmentRateLimitDB.id.in_(rate_ids)
                )
            )
        return len(mutation_ids) + len(rate_ids)


__all__ = ["SqlTurnObserverIdentityRepository", "TurnObserverIdentityRepositoryError"]
