"""Native Autopilot branching and fail-closed outcomes with actual SQL result admission."""

import base64
import copy
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from agent.db_models import TaskDB
from agent.routes.tasks.autopilot_native_dispatch import (
    NATIVE_DISPATCH_PORTS,
    NativeDispatchPorts,
    dispatch_native_task,
)
from agent.services.native_result_forwarding import validate_forwarded_native_result
from ananta_contracts.native_task_execution import verify_native_execution_request
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing, Ed25519VerificationKeyRing
from tests.bpmn.test_native_result_admission import native  # noqa: F401
from tests.pi.test_pi_result_completion_policy import admission, complete  # noqa: F401


@pytest.fixture
def dispatch(native, monkeypatch):  # noqa: F811
    from flask import Flask

    signer = Ed25519SigningKeyRing({"synthetic": base64.b64encode(b"s" * 32).decode()}, active_key_id="synthetic")
    verifier = Ed25519VerificationKeyRing.from_mapping(signer.verification_mapping())
    worker = SimpleNamespace(name="worker-1", role="worker", url="http://worker-1:5000", token="synthetic-" + "w"*40)
    with Session(native.engine) as session:
        task = session.get(TaskDB, native.task_id)
        task.assigned_agent_url = worker.url
        session.add(task)
        session.commit()
    task = native.repository.get_by_id(native.task_id)
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    calls, traces = [], []
    state = SimpleNamespace(response=copy.deepcopy(native.response), error=None, race=False, assignment=True)

    def forward(worker_url, endpoint, payload, **options):
        calls.append((worker_url, endpoint, payload, options))
        projected = verify_native_execution_request(payload, key_ring=verifier,
                                                     worker_id=worker.name, worker_url=worker.url)
        assert "assigned_agent_token" not in projected and "history" not in projected
        if state.race:
            complete(native)
        if state.error:
            raise RuntimeError(state.error)
        return state.response

    def accept(**values):
        validate_forwarded_native_result(task_id=native.task_id, dispatched_task=values["task"],
                                         response=values["response"], load_task=native.repository.get_by_id)
        native.response = values["response"]
        complete(native)

    def update(task_id, status, **values):
        row = native.repository.get_by_id(task_id)
        row.status = status
        for key, value in values.items():
            setattr(row, key, value)
        native.repository.save(row)

    def assignment(**_values):
        return SimpleNamespace(worker_id=worker.name, worker_url=worker.url, revision=1) if state.assignment else None

    from agent.services.native_dispatch_journal import SQLAlchemyNativeDispatchJournal
    from agent.services.native_result_task_projection import native_task_command

    app.extensions[NATIVE_DISPATCH_PORTS.extension_key] = NativeDispatchPorts(
        command=native_task_command, assignment=assignment, signing_keys=lambda: signer,
        transport=lambda: SimpleNamespace(forward_task=forward), accept_result=accept,
        journal=lambda: SQLAlchemyNativeDispatchJournal(native.engine),
    )
    # SQL fixture uses a deterministic 100-second authorization clock.
    monkeypatch.setattr("agent.routes.tasks.autopilot_native_dispatch.time.time", lambda: 100)
    dependencies = SimpleNamespace(
        repository_registry=lambda _app: SimpleNamespace(task_repo=native.repository),
        current_task_status=lambda tid, **_kwargs: native.repository.get_by_id(tid).status,
    )
    ctx = SimpleNamespace(task=task, target_worker=worker, app_ctx=app, dependencies=dependencies,
                          update_local_task_status=update,
                          append_trace_event=lambda tid, event, **values: traces.append((tid,event,values)))
    return SimpleNamespace(ctx=ctx, state=state, calls=calls, traces=traces, native=native)


def test_native_dispatch_commits_actual_hub_receipt_with_no_proposal(dispatch):
    result = dispatch_native_task(dispatch.ctx, policy={"execute_timeout": 60})
    assert result.dispatched and result.completed and not result.failed
    assert len(dispatch.calls) == 1 and dispatch.calls[0][3]["timeout"] == 30
    task = dispatch.native.repository.get_by_id(dispatch.native.task_id)
    assert task.verification_status["native_result_receipt"]["classification"] == "technical_observation"
    assert not task.last_proposal


def test_failed_preparation_cannot_reserve_dispatch_or_contact_worker(dispatch):
    from dataclasses import replace

    from sqlmodel import select

    from agent.db_models.native_dispatch import NativeDispatchDB

    def denied(**_values):
        raise ValueError('synthetic_preparation_denied')

    ports = dispatch.ctx.app_ctx.extensions[NATIVE_DISPATCH_PORTS.extension_key]
    dispatch.ctx.app_ctx.extensions[NATIVE_DISPATCH_PORTS.extension_key] = replace(ports, prepare_task=denied)
    result = dispatch_native_task(dispatch.ctx, policy={'execute_timeout': 30})
    assert result.failed and not result.completed and dispatch.calls == []
    with Session(dispatch.native.engine) as session:
        assert not session.exec(select(NativeDispatchDB)).all()


def test_preparation_cannot_rewrite_the_closed_native_command(dispatch):
    from dataclasses import replace

    from sqlmodel import select

    from agent.db_models.native_dispatch import NativeDispatchDB

    def mutate(**values):
        with Session(dispatch.native.engine) as session, session.begin():
            row = session.get(TaskDB, values['task_id'])
            context = copy.deepcopy(row.worker_execution_context)
            context['native_node_command']['control_task_id'] = 'synthetic-unapproved-controller'
            row.worker_execution_context = context
            session.add(row)

    ports = dispatch.ctx.app_ctx.extensions[NATIVE_DISPATCH_PORTS.extension_key]
    dispatch.ctx.app_ctx.extensions[NATIVE_DISPATCH_PORTS.extension_key] = replace(ports, prepare_task=mutate)
    result = dispatch_native_task(dispatch.ctx, policy={'execute_timeout': 30})
    assert result.failed and not result.completed and dispatch.calls == []
    with Session(dispatch.native.engine) as session:
        assert not session.exec(select(NativeDispatchDB)).all()


@pytest.mark.parametrize("failure", ["transport", "generic_success", "assignment", "hub_worker", "changed_command"])
def test_invalid_native_dispatch_never_falls_back_or_invents_terminal_receipt(dispatch, failure):
    if failure == "transport":
        dispatch.state.error = "private connection credentials"
    elif failure == "generic_success":
        dispatch.state.response = {"status": "completed", "output": "forged success", "exit_code": 0}
    elif failure == "assignment":
        dispatch.state.assignment = False
    elif failure == "hub_worker":
        dispatch.ctx.target_worker.role = "hub"
    else:
        with Session(dispatch.native.engine) as session:
            task = session.get(TaskDB, dispatch.native.task_id)
            task.worker_execution_context = {}
            session.add(task)
            session.commit()
    result = dispatch_native_task(dispatch.ctx, policy={"execute_timeout": 60})
    assert result.dispatched and result.failed and not result.completed
    assert result.failure_type == "native_autopilot_dispatch_blocked"
    task = dispatch.native.repository.get_by_id(dispatch.native.task_id)
    assert task.status == ("in_progress" if failure == "changed_command" else "waiting_for_review")
    assert "native_result_receipt" not in task.verification_status
    assert not task.last_proposal
    assert len(dispatch.calls) == (1 if failure in {"transport", "generic_success"} else 0)
    assert "private connection credentials" not in repr(dispatch.traces)


def test_late_transport_error_preserves_an_already_admitted_terminal_result(dispatch):
    dispatch.state.race = True
    dispatch.state.error = "unknown transport outcome"
    result = dispatch_native_task(dispatch.ctx, policy={"execute_timeout": 60})
    assert result.completed and not result.failed
    assert dispatch.native.repository.get_by_id(dispatch.native.task_id).status == "completed"


def test_generic_tasks_continue_to_existing_dispatch_phases():
    ctx = SimpleNamespace(task=SimpleNamespace(id="synthetic-generic", worker_execution_context={}))
    assert dispatch_native_task(ctx, policy={}) is None


def test_native_commands_are_never_replanned_by_the_context_splitter():
    from agent.routes.tasks.autopilot_task_dispatcher import _split_if_beyond_context

    task = SimpleNamespace(worker_execution_context={"runtime_path":"native_graph_node"})
    assert _split_if_beyond_context(task, app=SimpleNamespace(config={"AGENT_CONFIG": {
        "context_strategy": {"mode":"active"}}})) is None



def test_unknown_transport_is_not_sent_again_even_after_status_reset(dispatch):
    dispatch.state.error = "connection lost"
    first = dispatch_native_task(dispatch.ctx, policy={"execute_timeout": 60})
    assert first.failed and len(dispatch.calls) == 1
    # Even an external status reset does not constitute resend authorization.
    dispatch.ctx.update_local_task_status(dispatch.native.task_id, "todo")
    second = dispatch_native_task(dispatch.ctx, policy={"execute_timeout": 60})
    assert second.failed and second.failure_type == "native_autopilot_dispatch_blocked"
    assert len(dispatch.calls) == 1
