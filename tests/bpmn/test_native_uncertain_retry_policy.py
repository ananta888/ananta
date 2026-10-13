"""Actual Hub SQL dispatch history; explicit bounded retry preauthorization only."""

from types import SimpleNamespace

import pytest
from flask import Flask
from sqlmodel import Session, select

from agent.db_models.native_dispatch import NativeDispatchDB
from agent.services.native_dispatch_journal import SQLAlchemyNativeDispatchJournal
from agent.services.native_graph_production_composition import HubGovernedNativeControlPolicy
from agent.services.native_uncertain_retry_policy import authorize_uncertain_native_retry
from agent.services.workflow_worker_assignment_service import SQLAlchemyWorkflowWorkerAssignmentStore
from tests.bpmn.test_native_result_admission import native  # noqa: F401
from tests.pi.test_pi_result_completion_policy import admission  # noqa: F401

# ruff: noqa: F811 -- imported pytest fixtures.


@pytest.fixture
def retry(native):
    task = native.repository.get_by_id(native.task_id)
    assignments = SQLAlchemyWorkflowWorkerAssignmentStore(native.engine)
    assignment = assignments.get(tenant_id=task.tenant_id, run_id=native.command.run_id,
                                  step_id=native.command.node.node_id)
    journal = SQLAlchemyNativeDispatchJournal(native.engine)
    journal.begin(task=task, assignment=assignment)
    with Session(native.engine) as session:
        row = session.exec(select(NativeDispatchDB)).one()
    command = SimpleNamespace(command_type="retry", actor_id="synthetic-admin", run_id=native.command.run_id,
                              step_id=native.command.node.node_id)
    plan = SimpleNamespace(tenant_id=task.tenant_id, workflow_id=native.command.workflow_id,
                           plan_hash=native.command.plan_hash, policy_version=native.command.policy_version,
                           budget=SimpleNamespace(max_attempts=3))
    state = SimpleNamespace(status="cancelled", attempts={command.step_id: 1})
    binding = dict(purpose="native.retry.unknown_dispatch", tenant_id=plan.tenant_id,
                   workflow_id=plan.workflow_id, run_id=command.run_id, plan_hash=plan.plan_hash,
                   policy_version=plan.policy_version, step_id=command.step_id, actor_id=command.actor_id,
                   dispatch_binding_digest=row.binding_digest, maximum_attempts=2)
    return SimpleNamespace(native=native, command=command, plan=plan, state=state, binding=binding)


def decide(retry, bindings):
    return authorize_uncertain_native_retry(retry.command, plan=retry.plan, state=retry.state,
                                            engine=retry.native.engine, policy_bindings=bindings)


def test_unknown_outcome_requires_exact_hub_policy(retry):
    assert decide(retry, None) == (False, "native_retry_uncertain_dispatch_policy_required")
    assert decide(retry, [retry.binding]) == (True, "native_retry_uncertain_dispatch_preauthorized")


@pytest.mark.parametrize("field,value", [("actor_id", "foreign"), ("tenant_id", "foreign"),
                                         ("run_id", "foreign"), ("plan_hash", "b"*64),
                                         ("policy_version", "foreign"), ("step_id", "foreign"),
                                         ("dispatch_binding_digest", "a"*64), ("purpose", "retry"),
                                         ("maximum_attempts", True), ("maximum_attempts", 4)])
def test_partial_or_foreign_authorization_never_permits_retry(retry, field, value):
    binding = {**retry.binding, field: value}
    assert decide(retry, [binding])[0] is False


def test_attempt_budget_and_extra_fields_fail_closed(retry):
    assert decide(retry, [{**retry.binding, "allow_all": True}])[0] is False
    retry.state.attempts[retry.command.step_id] = 2
    assert decide(retry, [retry.binding])[0] is False


def test_targeting_different_step_cannot_evade_uncertain_run_history(retry):
    retry.command.step_id = "different-step"
    assert decide(retry, None)[0] is False


def test_production_control_policy_enforces_hub_context_and_config(retry, monkeypatch):
    policy = HubGovernedNativeControlPolicy()
    assert policy.authorize_command(retry.command, plan=retry.plan, state=retry.state)[0] is False
    monkeypatch.setattr("agent.database.engine", retry.native.engine)
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    with app.app_context():
        assert policy.authorize_command(retry.command, plan=retry.plan, state=retry.state)[0] is False
        app.config["ANANTA_NATIVE_UNCERTAIN_RETRY_POLICY"] = [retry.binding]
        assert policy.authorize_command(retry.command, plan=retry.plan, state=retry.state)[0] is True
        app.config["ROLE"] = "worker"
        assert policy.authorize_command(retry.command, plan=retry.plan, state=retry.state)[0] is False
