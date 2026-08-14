"""Persistent Hub revalidation for signed workflow authorization envelopes."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import WorkflowAuthorizationGrantDB
from agent.services.identity_validation import require_canonical_identity
from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.security import RuntimeAuthorizationEnvelope
from agent.services.workflow_runtime.sqlalchemy_support import (
    SessionFactory,
    SQLAlchemyStoreSupport,
)


@dataclass(frozen=True)
class WorkflowAuthorizationGrant:
    envelope_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    plan_hash: str
    policy_version: str
    grant_digest: str
    status: str
    revision: int
    issued_at: float
    expires_at: float
    updated_at: float
    revocation_reason: str = ""


class HubAuthorizationRevalidationPort(Protocol):
    def revalidate(self, envelope: RuntimeAuthorizationEnvelope) -> bool: ...


class WorkflowAuthorizationGrantPort(HubAuthorizationRevalidationPort, Protocol):
    def grant(self, envelope: RuntimeAuthorizationEnvelope) -> WorkflowAuthorizationGrant: ...

    def revoke(
        self,
        envelope_id: str,
        *,
        reason_code: str,
        expected_revision: int | None = None,
    ) -> WorkflowAuthorizationGrant: ...


class WorkflowAuthorizationGrantReadPort(Protocol):
    """Read one exact transition-owned grant without widening the mutation port."""

    def get(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        envelope_id: str,
    ) -> WorkflowAuthorizationGrant | None: ...


class UnavailableHubAuthorizationRevalidator:
    """Fail closed when the Hub grant store is not composed."""

    def revalidate(self, envelope: RuntimeAuthorizationEnvelope) -> bool:
        del envelope
        return False


class InMemoryWorkflowAuthorizationGrantService:
    def __init__(self, *, clock=time.time) -> None:
        self._values: dict[str, WorkflowAuthorizationGrant] = {}
        self._clock = clock
        self._lock = threading.RLock()

    def grant(self, envelope: RuntimeAuthorizationEnvelope) -> WorkflowAuthorizationGrant:
        envelope._assert_structure()
        candidate = _grant_from_envelope(envelope, timestamp=float(self._clock()))
        with self._lock:
            current = self._values.get(envelope.envelope_id)
            if current is not None:
                if current.grant_digest != candidate.grant_digest:
                    raise RuntimeError("workflow_authorization_grant_conflict")
                return current
            self._values[envelope.envelope_id] = candidate
            return candidate

    def revoke(
        self,
        envelope_id: str,
        *,
        reason_code: str,
        expected_revision: int | None = None,
    ) -> WorkflowAuthorizationGrant:
        if not str(reason_code).strip():
            raise ValueError("workflow_authorization_revocation_reason_required")
        with self._lock:
            current = self._values.get(str(envelope_id))
            if current is None:
                raise KeyError("workflow_authorization_grant_not_found")
            if expected_revision is not None and current.revision != int(expected_revision):
                raise RuntimeError("workflow_authorization_grant_cas_conflict")
            if current.status == "revoked":
                return current
            updated = WorkflowAuthorizationGrant(
                **{
                    **current.__dict__,
                    "status": "revoked",
                    "revision": current.revision + 1,
                    "updated_at": float(self._clock()),
                    "revocation_reason": str(reason_code),
                }
            )
            self._values[updated.envelope_id] = updated
            return updated

    def revalidate(self, envelope: RuntimeAuthorizationEnvelope) -> bool:
        with self._lock:
            current = self._values.get(envelope.envelope_id)
        return _grant_matches(current, envelope, now=float(self._clock()))

    def get(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        envelope_id: str,
    ) -> WorkflowAuthorizationGrant | None:
        binding = _grant_read_binding(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            envelope_id=envelope_id,
        )
        with self._lock:
            grant = self._values.get(binding[-1])
            if grant is None:
                return None
            _assert_grant_read_binding(grant, expected=binding)
            return grant


class SQLAlchemyWorkflowAuthorizationGrantService(SQLAlchemyStoreSupport):
    """Hub grant/revoke/revalidate service shared by all gateway instances."""

    def __init__(self, bind: Engine | SessionFactory, *, clock=time.time) -> None:
        super().__init__(bind)
        self._clock = clock

    def grant(self, envelope: RuntimeAuthorizationEnvelope) -> WorkflowAuthorizationGrant:
        envelope._assert_structure()
        timestamp = float(self._clock())
        candidate = _grant_from_envelope(envelope, timestamp=timestamp)
        try:
            with self._transaction() as session:
                current = session.get(WorkflowAuthorizationGrantDB, envelope.envelope_id)
                if current is not None:
                    stored = _grant_from_row(current)
                    if stored.grant_digest != candidate.grant_digest:
                        raise RuntimeError("workflow_authorization_grant_conflict")
                    return stored
                session.add(_grant_row(candidate))
                session.flush()
                return candidate
        except IntegrityError as exc:
            with self._read_session() as session:
                current = session.get(WorkflowAuthorizationGrantDB, envelope.envelope_id)
                if current is not None:
                    stored = _grant_from_row(current)
                    if stored.grant_digest == candidate.grant_digest:
                        return stored
            raise RuntimeError("workflow_authorization_grant_conflict") from exc

    def revoke(
        self,
        envelope_id: str,
        *,
        reason_code: str,
        expected_revision: int | None = None,
    ) -> WorkflowAuthorizationGrant:
        reason = str(reason_code).strip()
        if not reason:
            raise ValueError("workflow_authorization_revocation_reason_required")
        timestamp = float(self._clock())
        with self._transaction() as session:
            current = session.execute(
                self._for_update(
                    sa.select(WorkflowAuthorizationGrantDB).where(
                        WorkflowAuthorizationGrantDB.envelope_id == str(envelope_id)
                    )
                )
            ).scalar_one_or_none()
            if current is None:
                raise KeyError("workflow_authorization_grant_not_found")
            if expected_revision is not None and int(current.revision) != int(
                expected_revision
            ):
                raise RuntimeError("workflow_authorization_grant_cas_conflict")
            if current.status == "revoked":
                return _grant_from_row(current)
            current.status = "revoked"
            current.revision = int(current.revision) + 1
            current.updated_at = timestamp
            current.revoked_at = timestamp
            current.revocation_reason = reason
            session.flush()
            return _grant_from_row(current)

    def revalidate(self, envelope: RuntimeAuthorizationEnvelope) -> bool:
        try:
            envelope._assert_structure()
        except (TypeError, ValueError):
            return False
        with self._read_session() as session:
            current = session.get(WorkflowAuthorizationGrantDB, envelope.envelope_id)
            grant = _grant_from_row(current) if current is not None else None
        return _grant_matches(grant, envelope, now=float(self._clock()))

    def get(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        envelope_id: str,
    ) -> WorkflowAuthorizationGrant | None:
        binding = _grant_read_binding(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            envelope_id=envelope_id,
        )
        with self._read_session() as session:
            current = session.get(WorkflowAuthorizationGrantDB, binding[-1])
            if current is None:
                return None
            grant = _grant_from_row(current)
            _assert_grant_read_binding(grant, expected=binding)
            return grant


def _grant_from_envelope(
    envelope: RuntimeAuthorizationEnvelope,
    *,
    timestamp: float,
) -> WorkflowAuthorizationGrant:
    return WorkflowAuthorizationGrant(
        envelope_id=envelope.envelope_id,
        tenant_id=envelope.tenant_id,
        workflow_id=envelope.workflow_id,
        run_id=envelope.run_id,
        step_id=envelope.step_id,
        plan_hash=envelope.plan_hash,
        policy_version=envelope.policy_version,
        grant_digest=_grant_digest(envelope),
        status="active",
        revision=1,
        issued_at=envelope.issued_at,
        expires_at=envelope.expires_at,
        updated_at=timestamp,
    )


def _grant_matches(
    grant: WorkflowAuthorizationGrant | None,
    envelope: RuntimeAuthorizationEnvelope,
    *,
    now: float,
) -> bool:
    if grant is None or grant.status != "active" or grant.expires_at <= now:
        return False
    return bool(
        grant.envelope_id == envelope.envelope_id
        and grant.tenant_id == envelope.tenant_id
        and grant.workflow_id == envelope.workflow_id
        and grant.run_id == envelope.run_id
        and grant.step_id == envelope.step_id
        and grant.plan_hash == envelope.plan_hash
        and grant.policy_version == envelope.policy_version
        and grant.issued_at == envelope.issued_at
        and grant.expires_at == envelope.expires_at
        and grant.grant_digest == _grant_digest(envelope)
    )


def _grant_digest(envelope: RuntimeAuthorizationEnvelope) -> str:
    # The signature is already a MAC over the envelope. Hashing the complete
    # contract makes any tool, artifact, budget or binding widening fail closed.
    return sha256_json(envelope.to_dict())


def _grant_read_binding(
    *,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    step_id: str,
    envelope_id: str,
) -> tuple[str, str, str, str, str]:
    values = (
        require_canonical_identity(tenant_id, field_name="tenant_id"),
        require_canonical_identity(workflow_id, field_name="workflow_id"),
        require_canonical_identity(run_id, field_name="run_id"),
        require_canonical_identity(step_id, field_name="step_id"),
    )
    if (
        not isinstance(envelope_id, str)
        or not envelope_id
        or envelope_id != envelope_id.strip()
        or len(envelope_id) > 256
        or "\x00" in envelope_id
    ):
        raise ValueError("workflow_authorization_grant_envelope_id_invalid")
    return (*values, envelope_id)


def _assert_grant_read_binding(
    grant: WorkflowAuthorizationGrant,
    *,
    expected: tuple[str, str, str, str, str],
) -> None:
    actual = (
        grant.tenant_id,
        grant.workflow_id,
        grant.run_id,
        grant.step_id,
        grant.envelope_id,
    )
    if actual != expected:
        raise RuntimeError("workflow_authorization_grant_binding_conflict")


def _grant_row(grant: WorkflowAuthorizationGrant) -> WorkflowAuthorizationGrantDB:
    return WorkflowAuthorizationGrantDB(
        envelope_id=grant.envelope_id,
        tenant_id=grant.tenant_id,
        workflow_id=grant.workflow_id,
        run_id=grant.run_id,
        step_id=grant.step_id,
        plan_hash=grant.plan_hash,
        policy_version=grant.policy_version,
        grant_digest=grant.grant_digest,
        status=grant.status,
        revision=grant.revision,
        issued_at=grant.issued_at,
        expires_at=grant.expires_at,
        updated_at=grant.updated_at,
        revoked_at=None,
        revocation_reason=grant.revocation_reason,
    )


def _grant_from_row(row: WorkflowAuthorizationGrantDB) -> WorkflowAuthorizationGrant:
    return WorkflowAuthorizationGrant(
        envelope_id=str(row.envelope_id),
        tenant_id=str(row.tenant_id),
        workflow_id=str(row.workflow_id),
        run_id=str(row.run_id),
        step_id=str(row.step_id),
        plan_hash=str(row.plan_hash),
        policy_version=str(row.policy_version),
        grant_digest=str(row.grant_digest),
        status=str(row.status),
        revision=int(row.revision),
        issued_at=float(row.issued_at),
        expires_at=float(row.expires_at),
        updated_at=float(row.updated_at),
        revocation_reason=str(row.revocation_reason or ""),
    )


__all__ = [
    "HubAuthorizationRevalidationPort",
    "InMemoryWorkflowAuthorizationGrantService",
    "SQLAlchemyWorkflowAuthorizationGrantService",
    "UnavailableHubAuthorizationRevalidator",
    "WorkflowAuthorizationGrant",
    "WorkflowAuthorizationGrantPort",
    "WorkflowAuthorizationGrantReadPort",
]
