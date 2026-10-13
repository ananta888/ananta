"""Ownership claims validate the current control lease inside their recipient."""

import pytest
from sqlmodel import SQLModel, create_engine

from agent.services.bpmn_run_lease import BpmnRunLeaseService
from agent.services.workflow_runtime import HmacKeyRing
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.ownership import SQLiteExecutionOwnershipStore
from agent.services.workflow_runtime.persistence import SQLiteCheckpointStore
from agent.services.workflow_runtime.sqlalchemy_event_stores import SQLAlchemyCheckpointStore
from agent.services.workflow_runtime.sqlalchemy_ownership import SQLAlchemyExecutionOwnershipStore
from tests.bpmn.completion_helpers import linear_request


@pytest.fixture(params=["sqlalchemy", "sqlite"])
def recipient(request, tmp_path):
    path = tmp_path / "authority.sqlite3"
    engine = None
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{path}")
        SQLModel.metadata.create_all(engine)
        checkpoints = SQLAlchemyCheckpointStore(engine)
        ownership = SQLAlchemyExecutionOwnershipStore(engine)
    else:
        checkpoints = SQLiteCheckpointStore(path)
        ownership = SQLiteExecutionOwnershipStore(path)
    now = [100.0]
    keys = HmacKeyRing({"synthetic": "k" * 32}, active_key_id="synthetic")
    leases = BpmnRunLeaseService(checkpoints=checkpoints, keys=keys, clock=lambda: now[0])
    assignment = linear_request()
    yield ownership, leases, assignment, now
    if engine is not None:
        engine.dispose()
    else:
        ownership.close()
        checkpoints.close()


def claim(store, request, lease):
    return store.claim_fenced(
        lease=lease, tenant_id=request.plan.tenant_id, workflow_id=request.plan.workflow_id,
        run_id=request.run_id, step_id="work", owner_id="hub-owner", lease_seconds=60,
        maximum_retries=2, now=100,
    )


def test_current_claim_commits_and_exact_replay_does_not_create_new_attempt(recipient):
    store, leases, request, _ = recipient
    with leases.acquire(request) as lease:
        first = claim(store, request, lease)
        duplicate = claim(store, request, lease)
    assert first.acquired and not duplicate.acquired
    assert first.ownership == duplicate.ownership


def test_expiry_in_recipient_prevents_any_ownership_write(recipient, monkeypatch):
    store, leases, request, now = recipient
    validate = store._validate_control_lease

    def expire_inside_recipient(*args, **kwargs):
        now[0] = 131.0
        return validate(*args, **kwargs)

    with leases.acquire(request) as lease:
        lease.ensure_valid()
        monkeypatch.setattr(store, "_validate_control_lease", expire_inside_recipient)
        with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
            claim(store, request, lease)
    assert store.get(tenant_id=request.plan.tenant_id, run_id=request.run_id, step_id="work") is None


def test_superseded_control_lease_cannot_claim_unowned_step(recipient):
    store, leases, request, now = recipient
    with leases.acquire(request) as previous:
        now[0] = 131.0
        with leases.acquire(request) as successor:
            with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
                claim(store, request, previous)
            current = claim(store, request, successor)
    assert current.acquired


@pytest.mark.parametrize("field", ["tenant_id", "workflow_id", "run_id"])
def test_ownership_claim_cannot_broaden_recipient_workflow(recipient, field):
    store, leases, request, _ = recipient
    with leases.acquire(request) as lease:
        with pytest.raises(OptimisticConcurrencyError, match="binding_mismatch"):
            values = dict(
                tenant_id=request.plan.tenant_id, workflow_id=request.plan.workflow_id,
                run_id=request.run_id, step_id="work", owner_id="hub-owner", lease_seconds=60,
                maximum_retries=2, now=100,
            )
            values[field] = "foreign"
            store.claim_fenced(lease=lease, **values)
