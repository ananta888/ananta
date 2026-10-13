"""Grant issuance/revocation checks the signed control lease in its SQL transaction."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, create_engine

from agent.services.bpmn_run_lease import BpmnRunLeaseService
from agent.services.workflow_authorization_grant_service import SQLAlchemyWorkflowAuthorizationGrantService
from agent.services.workflow_runtime import HmacKeyRing, RuntimeAuthorizationEnvelope
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.sqlalchemy_event_stores import SQLAlchemyCheckpointStore
from tests.bpmn.completion_helpers import linear_request


@pytest.fixture
def grants(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'grants.sqlite3'}")
    SQLModel.metadata.create_all(engine)
    now = [100.0]
    keys = HmacKeyRing({"synthetic": "k" * 32}, active_key_id="synthetic")
    request = linear_request()
    service = SQLAlchemyWorkflowAuthorizationGrantService(engine, clock=lambda: now[0])
    leases = BpmnRunLeaseService(
        checkpoints=SQLAlchemyCheckpointStore(engine), keys=keys, clock=lambda: now[0],
    )
    envelope = RuntimeAuthorizationEnvelope.issue(
        key_ring=keys, tenant_id=request.plan.tenant_id, workflow_id=request.plan.workflow_id,
        run_id=request.run_id, step_id="work", plan_hash=request.plan.plan_hash,
        policy_version=request.plan.policy_version, ttl_seconds=200, now=100,
    )
    yield SimpleNamespace(service=service, leases=leases, request=request, envelope=envelope, now=now)
    engine.dispose()


def lookup(s):
    envelope = s.envelope
    return s.service.get(
        tenant_id=envelope.tenant_id, workflow_id=envelope.workflow_id, run_id=envelope.run_id,
        step_id=envelope.step_id, envelope_id=envelope.envelope_id,
    )


def test_current_grant_and_revoke_commit_idempotently(grants):
    s = grants
    with s.leases.acquire(s.request) as lease:
        original = s.service.grant_fenced(s.envelope, lease=lease)
        assert s.service.grant_fenced(s.envelope, lease=lease) == original
        revoked = s.service.revoke_fenced(s.envelope.envelope_id, lease=lease, reason_code="synthetic-cancel")
        assert revoked.status == "revoked" and revoked.revision == original.revision + 1
        assert s.service.revoke_fenced(
            s.envelope.envelope_id, lease=lease, reason_code="synthetic-cancel",
        ) == revoked


@pytest.mark.parametrize("operation", ["grant", "revoke"])
def test_expiry_inside_grant_recipient_rolls_back_every_write(grants, monkeypatch, operation):
    from agent.services.workflow_runtime import lease_fencing

    s = grants
    if operation == "revoke":
        s.service.grant(s.envelope)
    previous = lookup(s)
    validate = lease_fencing.validate_sqlalchemy_lease

    def expire_inside_recipient(*args, **kwargs):
        s.now[0] = 131.0
        return validate(*args, **kwargs)

    with s.leases.acquire(s.request) as lease:
        lease.ensure_valid()
        monkeypatch.setattr(lease_fencing, "validate_sqlalchemy_lease", expire_inside_recipient)
        with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
            if operation == "grant":
                s.service.grant_fenced(s.envelope, lease=lease)
            else:
                s.service.revoke_fenced(s.envelope.envelope_id, lease=lease, reason_code="synthetic-cancel")
    assert lookup(s) == previous


@pytest.mark.parametrize("operation", ["grant", "revoke"])
def test_successor_control_owner_fences_stale_grant_mutations(grants, operation):
    s = grants
    if operation == "revoke":
        s.service.grant(s.envelope)
    previous = lookup(s)
    with s.leases.acquire(s.request) as stale:
        s.now[0] = 131.0
        with s.leases.acquire(s.request):
            with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
                if operation == "grant":
                    s.service.grant_fenced(s.envelope, lease=stale)
                else:
                    s.service.revoke_fenced(s.envelope.envelope_id, lease=stale, reason_code="synthetic-cancel")
    assert lookup(s) == previous


@pytest.mark.parametrize("field", ["tenant_id", "workflow_id", "run_id"])
def test_control_lease_cannot_grant_or_revoke_foreign_scope(grants, field):
    s = grants
    foreign = replace(s.envelope, **{field: "foreign"})
    with s.leases.acquire(s.request) as lease:
        with pytest.raises(OptimisticConcurrencyError, match="binding_mismatch"):
            s.service.grant_fenced(foreign, lease=lease)
        s.service.grant(foreign)
        with pytest.raises(OptimisticConcurrencyError, match="binding_mismatch"):
            s.service.revoke_fenced(foreign.envelope_id, lease=lease, reason_code="synthetic-cancel")
