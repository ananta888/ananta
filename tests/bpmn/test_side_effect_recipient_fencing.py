"""Hub side-effect planning/authorization cannot outlive its control lease."""

from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, create_engine

from agent.services.bpmn_run_lease import BpmnRunLeaseService
from agent.services.workflow_runtime import HmacKeyRing
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.persistence import SQLiteCheckpointStore
from agent.services.workflow_runtime.side_effects import SQLiteSideEffectLedger, operation_id_for
from agent.services.workflow_runtime.sqlalchemy_event_stores import SQLAlchemyCheckpointStore
from agent.services.workflow_runtime.sqlalchemy_side_effects import SQLAlchemySideEffectLedger
from tests.bpmn.completion_helpers import linear_request


@pytest.fixture(params=["sqlalchemy", "sqlite"])
def effects(request, tmp_path):
    path = tmp_path / "side-effects.sqlite3"
    engine = None
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{path}")
        SQLModel.metadata.create_all(engine)
        checkpoints = SQLAlchemyCheckpointStore(engine)
        ledger = SQLAlchemySideEffectLedger(engine)
    else:
        checkpoints = SQLiteCheckpointStore(path)
        ledger = SQLiteSideEffectLedger(path)
    now = [100.0]
    keys = HmacKeyRing({"synthetic": "k" * 32}, active_key_id="synthetic")
    assignment = linear_request()
    values = dict(
        tenant_id=assignment.plan.tenant_id, workflow_id=assignment.plan.workflow_id,
        run_id=assignment.run_id, step_id="work", declared_operation="write-output",
        side_effect_class="idempotent_write",
    )
    leases = BpmnRunLeaseService(checkpoints=checkpoints, keys=keys, clock=lambda: now[0])
    yield SimpleNamespace(ledger=ledger, now=now, leases=leases, request=assignment, values=values, kind=request.param)
    if engine is not None:
        engine.dispose()
    else:
        ledger.close()
        checkpoints.close()


def authorize(s, record, lease):
    return s.ledger.authorize_fenced(
        record.operation_id, lease=lease, expected_revision=record.revision,
        fencing_token=1, authorization_envelope_id="synthetic-grant",
    )


def test_current_side_effect_plan_and_authorization_commit(effects):
    s = effects
    with s.leases.acquire(s.request) as lease:
        planned = s.ledger.plan_fenced(lease=lease, **s.values)
        assert s.ledger.plan_fenced(lease=lease, **s.values) == planned
        authorized = authorize(s, planned, lease)
    assert authorized.status == "authorized" and authorized.revision == planned.revision + 1


@pytest.mark.parametrize("operation", ["plan", "authorize"])
def test_expiry_inside_side_effect_recipient_prevents_mutation(effects, monkeypatch, operation):
    from agent.services.workflow_runtime import lease_fencing

    s = effects
    record = s.ledger.plan(**s.values) if operation == "authorize" else None
    function = "validate_sqlalchemy_lease" if s.kind == "sqlalchemy" else "validate_sqlite_lease"
    validate = getattr(lease_fencing, function)

    def expire_inside_recipient(*args, **kwargs):
        s.now[0] = 131.0
        return validate(*args, **kwargs)

    with s.leases.acquire(s.request) as lease:
        lease.ensure_valid()
        monkeypatch.setattr(lease_fencing, function, expire_inside_recipient)
        with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
            if operation == "plan":
                s.ledger.plan_fenced(lease=lease, **s.values)
            else:
                authorize(s, record, lease)
    if record is not None:
        assert s.ledger.get(tenant_id=s.values["tenant_id"], operation_id=record.operation_id) == record
    else:
        # Deterministic correlation key only, never an Evidence Registry ID.
        operation_id = operation_id_for(
            tenant_id=s.values["tenant_id"], run_id=s.values["run_id"], step_id=s.values["step_id"],
            declared_operation=s.values["declared_operation"],
        )
        assert s.ledger.get(tenant_id=s.values["tenant_id"], operation_id=operation_id) is None


@pytest.mark.parametrize("field", ["tenant_id", "workflow_id", "run_id"])
def test_control_lease_cannot_plan_or_authorize_foreign_side_effect(effects, field):
    s = effects
    values = {**s.values, field: "foreign"}
    with s.leases.acquire(s.request) as lease:
        with pytest.raises(OptimisticConcurrencyError, match="binding_mismatch"):
            s.ledger.plan_fenced(lease=lease, **values)
        foreign = s.ledger.plan(**values)
        with pytest.raises(OptimisticConcurrencyError, match="binding_mismatch"):
            authorize(s, foreign, lease)
    assert s.ledger.get(tenant_id=values["tenant_id"], operation_id=foreign.operation_id) == foreign
