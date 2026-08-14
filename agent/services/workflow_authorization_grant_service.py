"""Persistent Hub revalidation for signed workflow authorization envelopes."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

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
    revoked_at: float | None = None


class WorkflowAuthorizationGrantConflict(RuntimeError):
    """A proven grant identity, projection, or compare-and-set conflict."""


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


@runtime_checkable
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


@runtime_checkable
class WorkflowTransitionAuthorizationGrantCommitPort(Protocol):
    """Deterministic grant commit capability for one Hub transition effect."""

    def commit_transition_grant(
        self,
        envelope: RuntimeAuthorizationEnvelope,
        *,
        recorded_at: float,
    ) -> WorkflowAuthorizationGrant: ...


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
        return self.commit_transition_grant(
            envelope,
            recorded_at=float(self._clock()),
        )

    def commit_transition_grant(
        self,
        envelope: RuntimeAuthorizationEnvelope,
        *,
        recorded_at: float,
    ) -> WorkflowAuthorizationGrant:
        envelope._assert_structure()
        candidate = _grant_from_envelope(
            envelope,
            timestamp=_positive_recorded_at(recorded_at),
        )
        with self._lock:
            current = self._values.get(envelope.envelope_id)
            if current is not None:
                assert_workflow_authorization_grant_projection(current)
                if current.grant_digest != candidate.grant_digest:
                    raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_conflict")
                _assert_same_grant_issuance(current, candidate)
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
        reason = str(reason_code).strip()
        if not reason:
            raise ValueError("workflow_authorization_revocation_reason_required")
        with self._lock:
            current = self._values.get(str(envelope_id))
            if current is None:
                raise KeyError("workflow_authorization_grant_not_found")
            assert_workflow_authorization_grant_projection(current)
            if expected_revision is not None and current.revision != int(expected_revision):
                raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_cas_conflict")
            if current.status == "revoked":
                return current
            timestamp = float(self._clock())
            updated = WorkflowAuthorizationGrant(
                **{
                    **current.__dict__,
                    "status": "revoked",
                    "revision": current.revision + 1,
                    "updated_at": timestamp,
                    "revoked_at": timestamp,
                    "revocation_reason": reason,
                }
            )
            assert_workflow_authorization_grant_projection(updated)
            self._values[updated.envelope_id] = updated
            return updated

    def revalidate(self, envelope: RuntimeAuthorizationEnvelope) -> bool:
        try:
            envelope._assert_structure()
        except (AttributeError, TypeError, ValueError):
            return False
        with self._lock:
            current = self._values.get(envelope.envelope_id)
        try:
            return _grant_matches(current, envelope, now=float(self._clock()))
        except RuntimeError:
            return False

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
            assert_workflow_authorization_grant_projection(grant)
            _assert_grant_read_binding(grant, expected=binding)
            return grant


class SQLAlchemyWorkflowAuthorizationGrantService(SQLAlchemyStoreSupport):
    """Hub grant/revoke/revalidate service shared by all gateway instances."""

    def __init__(self, bind: Engine | SessionFactory, *, clock=time.time) -> None:
        super().__init__(bind)
        self._clock = clock

    def grant(self, envelope: RuntimeAuthorizationEnvelope) -> WorkflowAuthorizationGrant:
        envelope._assert_structure()
        return self.commit_transition_grant(
            envelope,
            recorded_at=float(self._clock()),
        )

    def commit_transition_grant(
        self,
        envelope: RuntimeAuthorizationEnvelope,
        *,
        recorded_at: float,
    ) -> WorkflowAuthorizationGrant:
        envelope._assert_structure()
        candidate = _grant_from_envelope(
            envelope,
            timestamp=_positive_recorded_at(recorded_at),
        )
        try:
            with self._transaction() as session:
                current = session.get(WorkflowAuthorizationGrantDB, envelope.envelope_id)
                if current is not None:
                    stored = _grant_from_row(current)
                    if stored.grant_digest != candidate.grant_digest:
                        raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_conflict")
                    _assert_same_grant_issuance(stored, candidate)
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
                        _assert_same_grant_issuance(stored, candidate)
                        return stored
            raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_conflict") from exc

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
            stored = _grant_from_row(current)
            if expected_revision is not None and stored.revision != int(expected_revision):
                raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_cas_conflict")
            if stored.status == "revoked":
                return stored
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
        except (AttributeError, TypeError, ValueError):
            return False
        with self._read_session() as session:
            current = session.get(WorkflowAuthorizationGrantDB, envelope.envelope_id)
            try:
                grant = _grant_from_row(current) if current is not None else None
            except RuntimeError:
                return False
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
    if timestamp < envelope.issued_at:
        raise ValueError("workflow_authorization_grant_recorded_at_invalid")
    grant = WorkflowAuthorizationGrant(
        envelope_id=envelope.envelope_id,
        tenant_id=envelope.tenant_id,
        workflow_id=envelope.workflow_id,
        run_id=envelope.run_id,
        step_id=envelope.step_id,
        plan_hash=envelope.plan_hash,
        policy_version=envelope.policy_version,
        grant_digest=workflow_authorization_grant_digest(envelope),
        status="active",
        revision=1,
        issued_at=envelope.issued_at,
        expires_at=envelope.expires_at,
        updated_at=timestamp,
    )
    assert_workflow_authorization_grant_projection(grant)
    return grant


def _grant_matches(
    grant: WorkflowAuthorizationGrant | None,
    envelope: RuntimeAuthorizationEnvelope,
    *,
    now: float,
) -> bool:
    if grant is None:
        return False
    assert_workflow_authorization_grant_projection(grant)
    if grant.status != "active" or grant.expires_at <= now:
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
        and grant.grant_digest == workflow_authorization_grant_digest(envelope)
    )


def workflow_authorization_grant_digest(
    envelope: RuntimeAuthorizationEnvelope,
) -> str:
    """Hash every signed-envelope field without redaction or upcasting."""

    if not isinstance(envelope, RuntimeAuthorizationEnvelope):
        raise TypeError("workflow_authorization_grant_envelope_invalid")
    envelope._assert_structure()
    # The signature already authenticates the envelope. Hashing the complete
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


def _positive_recorded_at(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value <= 0
        or value > 2**63 - 1
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        raise ValueError("workflow_authorization_grant_recorded_at_invalid")
    return float(value)


def assert_workflow_authorization_grant_projection(
    grant: WorkflowAuthorizationGrant,
) -> WorkflowAuthorizationGrant:
    """Reject coercible or semantically impossible current-row projections."""

    if not isinstance(grant, WorkflowAuthorizationGrant):
        raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_projection_conflict")
    for name in (
        "envelope_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "step_id",
        "plan_hash",
        "policy_version",
        "grant_digest",
        "status",
        "revocation_reason",
    ):
        value = getattr(grant, name)
        if not isinstance(value, str) or "\x00" in value:
            raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_projection_conflict")
    if (
        not grant.envelope_id
        or grant.envelope_id != grant.envelope_id.strip()
        or len(grant.envelope_id) > 256
        or not grant.tenant_id
        or not grant.workflow_id
        or not grant.run_id
        or not grant.step_id
        or not grant.plan_hash
        or not grant.policy_version
        or len(grant.grant_digest) != 64
        or any(character not in "0123456789abcdef" for character in grant.grant_digest)
        or isinstance(grant.revision, bool)
        or not isinstance(grant.revision, int)
    ):
        raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_projection_conflict")
    for timestamp in (grant.issued_at, grant.expires_at, grant.updated_at):
        if type(timestamp) is not float or not math.isfinite(timestamp):
            raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_projection_conflict")
    if grant.issued_at <= 0 or grant.expires_at <= grant.issued_at or grant.updated_at < grant.issued_at:
        raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_projection_conflict")
    if grant.status == "active":
        if grant.revision != 1 or grant.revoked_at is not None or grant.revocation_reason:
            raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_projection_conflict")
        return grant
    if grant.status == "revoked":
        if (
            grant.revision != 2
            or type(grant.revoked_at) is not float
            or not math.isfinite(grant.revoked_at)
            or grant.revoked_at <= 0
            or grant.updated_at != grant.revoked_at
            or grant.revoked_at < grant.issued_at
            or not grant.revocation_reason
            or grant.revocation_reason != grant.revocation_reason.strip()
        ):
            raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_projection_conflict")
        return grant
    raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_projection_conflict")


def _assert_same_grant_issuance(
    current: WorkflowAuthorizationGrant,
    candidate: WorkflowAuthorizationGrant,
) -> None:
    immutable_fields = (
        "envelope_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "step_id",
        "plan_hash",
        "policy_version",
        "grant_digest",
        "issued_at",
        "expires_at",
    )
    if any(getattr(current, name) != getattr(candidate, name) for name in immutable_fields):
        raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_projection_conflict")


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
        raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_binding_conflict")


def _grant_row(grant: WorkflowAuthorizationGrant) -> WorkflowAuthorizationGrantDB:
    assert_workflow_authorization_grant_projection(grant)
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
        revoked_at=grant.revoked_at,
        revocation_reason=grant.revocation_reason,
    )


def _grant_from_row(row: WorkflowAuthorizationGrantDB) -> WorkflowAuthorizationGrant:
    grant = WorkflowAuthorizationGrant(
        envelope_id=row.envelope_id,
        tenant_id=row.tenant_id,
        workflow_id=row.workflow_id,
        run_id=row.run_id,
        step_id=row.step_id,
        plan_hash=row.plan_hash,
        policy_version=row.policy_version,
        grant_digest=row.grant_digest,
        status=row.status,
        revision=row.revision,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        updated_at=row.updated_at,
        revoked_at=row.revoked_at,
        revocation_reason=row.revocation_reason,
    )
    assert_workflow_authorization_grant_projection(grant)
    projected = (
        row.envelope_id,
        row.tenant_id,
        row.workflow_id,
        row.run_id,
        row.step_id,
        row.plan_hash,
        row.policy_version,
        row.grant_digest,
        row.status,
        row.revision,
        row.issued_at,
        row.expires_at,
        row.updated_at,
        row.revoked_at,
        row.revocation_reason,
    )
    exact = (
        grant.envelope_id,
        grant.tenant_id,
        grant.workflow_id,
        grant.run_id,
        grant.step_id,
        grant.plan_hash,
        grant.policy_version,
        grant.grant_digest,
        grant.status,
        grant.revision,
        grant.issued_at,
        grant.expires_at,
        grant.updated_at,
        grant.revoked_at,
        grant.revocation_reason,
    )
    if projected != exact:
        raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_projection_conflict")
    return grant


__all__ = [
    "HubAuthorizationRevalidationPort",
    "InMemoryWorkflowAuthorizationGrantService",
    "SQLAlchemyWorkflowAuthorizationGrantService",
    "UnavailableHubAuthorizationRevalidator",
    "WorkflowAuthorizationGrant",
    "WorkflowAuthorizationGrantConflict",
    "WorkflowAuthorizationGrantPort",
    "WorkflowAuthorizationGrantReadPort",
    "WorkflowTransitionAuthorizationGrantCommitPort",
    "assert_workflow_authorization_grant_projection",
    "workflow_authorization_grant_digest",
]
