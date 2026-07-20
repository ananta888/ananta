"""Persistence boundary for Hub-owned semantic compute contracts."""

from __future__ import annotations

import hashlib
import time
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import (
    SemanticComputeContractDB,
    SemanticContractMutationDB,
    SemanticSessionMembershipDB,
)
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent


class SemanticContractRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SemanticPrincipal:
    tenant_id: str
    subject: str


@dataclass(frozen=True, slots=True)
class ContractMutation:
    operation: str
    idempotency_key: str
    request_digest: str
    expected_revision: int
    expected_digest: str
    payload: Mapping[str, Any]
    status: str
    activate: bool = False
    negotiation_started_at_ms: int | None = None
    negotiation_round_count: int | None = None
    negotiation_message_count: int | None = None


class ContractLeaseTransactionPort(Protocol):
    """Narrow in-session cascade port owned by the Hub lease authority."""

    def revoke_contract_active_in_session(
        self,
        db: Session,
        *,
        tenant_id: str,
        owner_subject: str,
        contract_id: str,
    ) -> int: ...

    def transaction_fence(self) -> AbstractContextManager[None]: ...


class SemanticContractRepository:
    """Atomic tenant-scoped repository with durable idempotency and CAS."""

    def __init__(self, *, db_engine=default_engine, clock=time.time) -> None:
        self._engine = db_engine
        self._clock = clock

    def put_membership(
        self,
        principal: SemanticPrincipal,
        *,
        session_id: str,
        epoch: int,
        role: str = "participant",
        permissions: Mapping[str, Any] | None = None,
        room_id: str | None = None,
        expires_at: float | None = None,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SemanticSessionMembershipDB:
        now = self._clock()
        with Session(self._engine) as db:
            existing = db.exec(
                select(SemanticSessionMembershipDB).where(
                    SemanticSessionMembershipDB.tenant_id == principal.tenant_id,
                    SemanticSessionMembershipDB.session_id == session_id,
                    SemanticSessionMembershipDB.member_subject == principal.subject,
                    SemanticSessionMembershipDB.epoch == epoch,
                )
            ).first()
            if existing is None:
                existing = SemanticSessionMembershipDB(
                    tenant_id=principal.tenant_id,
                    session_id=session_id,
                    room_id=room_id,
                    member_subject=principal.subject,
                    role=role,
                    epoch=epoch,
                    permissions=dict(permissions or {}),
                    expires_at=expires_at,
                )
            else:
                existing.revision += 1
                existing.role = role
                existing.permissions = dict(permissions or {})
                existing.room_id = room_id
                existing.expires_at = expires_at
                existing.status = "active"
                existing.updated_at = now
            db.add(existing)
            if audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(db, audit_event)
            db.commit()
            db.refresh(existing)
            return existing

    def require_membership(
        self, principal: SemanticPrincipal, *, session_id: str, epoch: int, permission: str
    ) -> SemanticSessionMembershipDB:
        now = self._clock()
        with Session(self._engine) as db:
            item = db.exec(
                select(SemanticSessionMembershipDB).where(
                    SemanticSessionMembershipDB.tenant_id == principal.tenant_id,
                    SemanticSessionMembershipDB.session_id == session_id,
                    SemanticSessionMembershipDB.member_subject == principal.subject,
                    SemanticSessionMembershipDB.epoch == epoch,
                    SemanticSessionMembershipDB.status == "active",
                )
            ).first()
            if item is None or (item.expires_at is not None and item.expires_at <= now):
                raise SemanticContractRepositoryError("session_not_found")
            if item.role != "owner" and not bool((item.permissions or {}).get(permission)):
                # Deliberately hide membership and session existence.
                raise SemanticContractRepositoryError("session_not_found")
            return item

    def create(
        self,
        principal: SemanticPrincipal,
        *,
        contract_id: str,
        request_digest: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        status: str,
        negotiation_started_at_ms: int | None = None,
        negotiation_round_count: int = 1,
        negotiation_message_count: int = 1,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> tuple[SemanticComputeContractDB, bool]:
        operation = f"create:{payload['session_id']}:{payload['epoch']}"
        with Session(self._engine) as db:
            replay = self._replay(db, principal, operation, idempotency_key, request_digest)
            if replay is not None:
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(db, audit_event)
                    db.commit()
                return self._replay_contract(principal, replay), True
            started_at_ms = (
                int(negotiation_started_at_ms) if negotiation_started_at_ms is not None else int(self._clock() * 1_000)
            )
            self._validate_initial_negotiation_budget(
                started_at_ms=started_at_ms,
                round_count=negotiation_round_count,
                message_count=negotiation_message_count,
            )
            item = SemanticComputeContractDB(
                id=contract_id,
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                session_id=str(payload["session_id"]),
                room_id=str(payload["room_id"]) if payload.get("room_id") else None,
                epoch=int(payload["epoch"]),
                revision=int(payload["revision"]),
                digest=str(payload["contract_digest"]),
                status=status,
                profile=str(payload["profile"]),
                security_mode=str(payload["security_mode"]),
                consent_version=int(payload["consent_version"]),
                policy_version=str(payload["policy_version"]),
                negotiation_started_at_ms=started_at_ms,
                negotiation_round_count=negotiation_round_count,
                negotiation_message_count=negotiation_message_count,
                contract_payload=dict(payload),
                expires_at=float(payload["expires_at_ms"]) / 1000.0,
            )
            db.add(item)
            self._record_mutation(db, principal, operation, idempotency_key, request_digest, item)
            if audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(db, audit_event)
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                replay = self._replay(db, principal, operation, idempotency_key, request_digest)
                if replay is not None:
                    return self._replay_contract(principal, replay), True
                raise SemanticContractRepositoryError("contract_revision_conflict") from exc
            db.refresh(item)
            return item, False

    def mutate(
        self,
        principal: SemanticPrincipal,
        *,
        contract_id: str,
        mutation: ContractMutation,
        audit_event: SemanticMediaAuditEvent | None = None,
        lease_revoker: ContractLeaseTransactionPort | None = None,
    ) -> tuple[SemanticComputeContractDB, bool]:
        operation = f"{mutation.operation}:{contract_id}"
        key_digest = self._key_digest(mutation.idempotency_key)
        write_fence = lease_revoker.transaction_fence() if lease_revoker is not None else nullcontext()
        with write_fence, Session(self._engine) as db:
            replay = self._replay_digest(db, principal, operation, key_digest, mutation.request_digest)
            if replay is not None:
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(db, audit_event)
                    db.commit()
                return self._replay_contract(principal, replay), True
            current = self._scoped_get(db, principal, contract_id, for_update=True)
            if current.revision != mutation.expected_revision:
                raise SemanticContractRepositoryError("stale_revision")
            if current.digest != mutation.expected_digest:
                raise SemanticContractRepositoryError("stale_digest")
            if current.status == "revoked":
                raise SemanticContractRepositoryError("contract_revoked")
            started_at_ms, round_count, message_count = self._next_negotiation_budget(
                current,
                mutation,
            )
            now = self._clock()
            new_payload = dict(mutation.payload)
            next_revision = current.revision + 1
            if int(new_payload.get("revision", -1)) != next_revision:
                raise SemanticContractRepositoryError("invalid_next_revision")
            active_scope_key = current.active_scope_key
            if mutation.activate:
                scope = self._active_scope(current.tenant_id, current.session_id, current.epoch)
                conflict = db.exec(
                    select(SemanticComputeContractDB).where(
                        SemanticComputeContractDB.active_scope_key == scope,
                        SemanticComputeContractDB.id != current.id,
                    )
                ).first()
                if conflict is not None:
                    raise SemanticContractRepositoryError("active_contract_conflict")
                active_scope_key = scope
            if mutation.status == "revoked":
                active_scope_key = None
            result = db.exec(
                sa.update(SemanticComputeContractDB)
                .where(
                    SemanticComputeContractDB.id == current.id,
                    SemanticComputeContractDB.tenant_id == principal.tenant_id,
                    SemanticComputeContractDB.owner_subject == principal.subject,
                    SemanticComputeContractDB.revision == mutation.expected_revision,
                    SemanticComputeContractDB.digest == mutation.expected_digest,
                )
                .values(
                    revision=next_revision,
                    digest=str(new_payload["contract_digest"]),
                    status=mutation.status,
                    profile=str(new_payload["profile"]),
                    security_mode=str(new_payload["security_mode"]),
                    consent_version=int(new_payload["consent_version"]),
                    policy_version=str(new_payload["policy_version"]),
                    negotiation_started_at_ms=started_at_ms,
                    negotiation_round_count=round_count,
                    negotiation_message_count=message_count,
                    contract_payload=new_payload,
                    active_scope_key=active_scope_key,
                    expires_at=float(new_payload["expires_at_ms"]) / 1000.0,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise SemanticContractRepositoryError("stale_revision")
            current = self._scoped_get(db, principal, contract_id)
            self._record_mutation_digest(db, principal, operation, key_digest, mutation.request_digest, current)
            if audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(db, audit_event)
            if lease_revoker is not None:
                try:
                    lease_revoker.revoke_contract_active_in_session(
                        db,
                        tenant_id=principal.tenant_id,
                        owner_subject=principal.subject,
                        contract_id=contract_id,
                    )
                except Exception as exc:
                    raise SemanticContractRepositoryError("lease_revocation_unavailable") from exc
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                replay = self._replay_digest(db, principal, operation, key_digest, mutation.request_digest)
                if replay is not None:
                    return self._replay_contract(principal, replay), True
                raise SemanticContractRepositoryError("contract_revision_conflict") from exc
            db.refresh(current)
            return current, False

    def get(self, principal: SemanticPrincipal, contract_id: str) -> SemanticComputeContractDB:
        with Session(self._engine) as db:
            return self._scoped_get(db, principal, contract_id)

    def get_create_replay(
        self,
        principal: SemanticPrincipal,
        *,
        session_id: str,
        epoch: int,
        idempotency_key: str,
        request_digest: str,
    ) -> SemanticComputeContractDB | None:
        """Return the durable create result without re-running negotiation policy."""

        operation = f"create:{session_id}:{epoch}"
        with Session(self._engine) as db:
            receipt = self._replay(
                db,
                principal,
                operation,
                idempotency_key,
                request_digest,
            )
            return self._replay_contract(principal, receipt) if receipt is not None else None

    def get_mutation_replay(
        self,
        principal: SemanticPrincipal,
        *,
        contract_id: str,
        operation: str,
        idempotency_key: str,
        request_digest: str,
    ) -> SemanticComputeContractDB | None:
        """Return an exactly-once mutation result, including its budget snapshot."""

        with Session(self._engine) as db:
            receipt = self._replay(
                db,
                principal,
                f"{operation}:{contract_id}",
                idempotency_key,
                request_digest,
            )
            return self._replay_contract(principal, receipt) if receipt is not None else None

    def list(
        self, principal: SemanticPrincipal, *, session_id: str | None = None, offset: int = 0, limit: int = 50
    ) -> list[SemanticComputeContractDB]:
        if not 0 <= offset <= 10_000_000 or not 1 <= limit <= 100:
            raise SemanticContractRepositoryError("pagination_out_of_bounds")
        with Session(self._engine) as db:
            statement = select(SemanticComputeContractDB).where(
                SemanticComputeContractDB.tenant_id == principal.tenant_id,
                SemanticComputeContractDB.owner_subject == principal.subject,
            )
            if session_id:
                statement = statement.where(SemanticComputeContractDB.session_id == session_id)
            page = statement.order_by(SemanticComputeContractDB.created_at.desc()).offset(offset).limit(limit)
            return list(db.exec(page))

    @staticmethod
    def _active_scope(tenant_id: str, session_id: str, epoch: int) -> str:
        return hashlib.sha256(f"{tenant_id}\0{session_id}\0{epoch}".encode()).hexdigest()

    @staticmethod
    def _validate_initial_negotiation_budget(
        *,
        started_at_ms: int,
        round_count: int,
        message_count: int,
    ) -> None:
        if started_at_ms < 0 or round_count != 1 or message_count != 1:
            raise SemanticContractRepositoryError("negotiation_budget_invalid")

    @staticmethod
    def _next_negotiation_budget(
        current: SemanticComputeContractDB,
        mutation: ContractMutation,
    ) -> tuple[int, int, int]:
        """Validate the next budget snapshot against the row locked for CAS.

        An offer opens round one.  A counter opens one further round; accept,
        activate, fallback and revoke are messages in the current round.  All
        authoritative mutations consume exactly one message.
        """

        expected_started_at_ms = int(current.negotiation_started_at_ms)
        expected_round_count = int(current.negotiation_round_count) + (1 if mutation.operation == "counter" else 0)
        expected_message_count = int(current.negotiation_message_count) + 1
        values = (
            expected_started_at_ms
            if mutation.negotiation_started_at_ms is None
            else int(mutation.negotiation_started_at_ms),
            expected_round_count if mutation.negotiation_round_count is None else int(mutation.negotiation_round_count),
            expected_message_count
            if mutation.negotiation_message_count is None
            else int(mutation.negotiation_message_count),
        )
        if values != (
            expected_started_at_ms,
            expected_round_count,
            expected_message_count,
        ):
            raise SemanticContractRepositoryError("negotiation_budget_conflict")
        return values

    @staticmethod
    def _key_digest(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def _scoped_get(
        self,
        db: Session,
        principal: SemanticPrincipal,
        contract_id: str,
        *,
        for_update: bool = False,
    ) -> SemanticComputeContractDB:
        statement = select(SemanticComputeContractDB).where(
            SemanticComputeContractDB.id == contract_id,
            SemanticComputeContractDB.tenant_id == principal.tenant_id,
            SemanticComputeContractDB.owner_subject == principal.subject,
        )
        if for_update:
            statement = statement.with_for_update()
        item = db.exec(statement).first()
        if item is None:
            raise SemanticContractRepositoryError("contract_not_found")
        return item

    def _replay(
        self, db: Session, principal: SemanticPrincipal, operation: str, key: str, request_digest: str
    ) -> SemanticContractMutationDB | None:
        return self._replay_digest(db, principal, operation, self._key_digest(key), request_digest)

    @staticmethod
    def _replay_digest(
        db: Session,
        principal: SemanticPrincipal,
        operation: str,
        key_digest: str,
        request_digest: str,
    ) -> SemanticContractMutationDB | None:
        receipt = db.exec(
            select(SemanticContractMutationDB).where(
                SemanticContractMutationDB.tenant_id == principal.tenant_id,
                SemanticContractMutationDB.owner_subject == principal.subject,
                SemanticContractMutationDB.operation == operation,
                SemanticContractMutationDB.idempotency_key_digest == key_digest,
            )
        ).first()
        if receipt is not None and receipt.request_digest != request_digest:
            raise SemanticContractRepositoryError("idempotency_conflict")
        return receipt

    def _record_mutation(
        self,
        db: Session,
        principal: SemanticPrincipal,
        operation: str,
        key: str,
        request_digest: str,
        item: SemanticComputeContractDB,
    ) -> None:
        self._record_mutation_digest(db, principal, operation, self._key_digest(key), request_digest, item)

    @staticmethod
    def _record_mutation_digest(
        db: Session,
        principal: SemanticPrincipal,
        operation: str,
        key_digest: str,
        request_digest: str,
        item: SemanticComputeContractDB,
    ) -> None:
        db.add(
            SemanticContractMutationDB(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                operation=operation,
                idempotency_key_digest=key_digest,
                request_digest=request_digest,
                contract_id=item.id,
                result_revision=item.revision,
                result_status=item.status,
                result_digest=item.digest,
                result_negotiation_started_at_ms=item.negotiation_started_at_ms,
                result_negotiation_round_count=item.negotiation_round_count,
                result_negotiation_message_count=item.negotiation_message_count,
                result_payload=dict(item.contract_payload or {}),
            )
        )

    @staticmethod
    def _replay_contract(
        principal: SemanticPrincipal, receipt: SemanticContractMutationDB
    ) -> SemanticComputeContractDB:
        payload = dict(receipt.result_payload or {})
        return SemanticComputeContractDB(
            id=receipt.contract_id,
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            session_id=str(payload["session_id"]),
            room_id=str(payload["room_id"]) if payload.get("room_id") else None,
            epoch=int(payload["epoch"]),
            revision=receipt.result_revision,
            digest=receipt.result_digest,
            status=receipt.result_status,
            profile=str(payload["profile"]),
            security_mode=str(payload["security_mode"]),
            consent_version=int(payload["consent_version"]),
            policy_version=str(payload["policy_version"]),
            negotiation_started_at_ms=receipt.result_negotiation_started_at_ms,
            negotiation_round_count=receipt.result_negotiation_round_count,
            negotiation_message_count=receipt.result_negotiation_message_count,
            contract_payload=payload,
            expires_at=float(payload["expires_at_ms"]) / 1000.0,
        )


_repository: SemanticContractRepository | None = None


def get_semantic_contract_repository() -> SemanticContractRepository:
    global _repository
    if _repository is None:
        _repository = SemanticContractRepository()
    return _repository


__all__ = [
    "ContractMutation",
    "SemanticContractRepository",
    "SemanticContractRepositoryError",
    "SemanticPrincipal",
    "get_semantic_contract_repository",
]
