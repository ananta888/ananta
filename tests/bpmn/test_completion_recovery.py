"""Adversarial crash windows on SQL-backed Native BPMN components.

These assert required recovery behavior, including currently unfixed cases;
no xfail masks an unsafe or unrecoverable transition.
"""

import pytest

from tests.bpmn.completion_helpers import SimulatedHubCrash, crash_once, linear_request
from tests.bpmn.completion_helpers import harness as harness
from tests.bpmn.test_execution_recovery import advance_bounded
from tests.test_native_graph_runtime import signed_control


@pytest.mark.parametrize("after", [False, True], ids=["before", "after"])
@pytest.mark.parametrize("boundary", ["queue", "delegated-event", "dispatch-checkpoint"])
def test_dispatch_crash_recovers_the_same_assignment(harness, monkeypatch, boundary, after):
    request = linear_request()
    harness.hub.start(request)  # Start control is durable; work is next.
    if boundary == "queue":
        target, method, predicate = harness.queue, "submit_fenced", lambda *a, **k: True
    elif boundary == "delegated-event":
        target, method = harness.stores["events"], "append_fenced"

        def predicate(event, **kwargs):
            return event.event_type == "workflow.step.delegated"
    else:
        target, method = harness.stores["checkpoints"], "save_fenced"

        def predicate(checkpoint, **kwargs):
            return checkpoint.task_id == request.control_task_id and "work" in checkpoint.state.runtime_metadata.get(
                "running", {}
            )

    crash_once(monkeypatch, target, method, after=after, predicate=predicate)
    with pytest.raises(SimulatedHubCrash):
        harness.hub.advance(request)
    harness.restart()
    result = harness.finish(request)
    if boundary == "queue" and not after:
        assert result.status == "failed" and result.reason_code
        assert harness.handler.calls == []
        assert harness.hub.advance(request).checkpoint == result.checkpoint
        return  # No durable queue admission exists; bounded failure is intentional.
    assert result.status == "completed", result.reason_code
    assert harness.handler.calls == ["work", "after"]
    events = harness.hub.stream(request)
    assert len([event for event in events if event.event_type == "workflow.step.delegated"]) == 2


@pytest.mark.parametrize("after", [False, True], ids=["before", "after"])
@pytest.mark.parametrize("boundary", ["result-poll", "result-ack", "completion-event", "result-checkpoint"])
def test_result_crash_recovers_without_reexecuting_worker(harness, monkeypatch, boundary, after):
    request = linear_request()
    harness.dispatch_first(request)
    if boundary == "result-poll":
        target, method, predicate = harness.queue, "poll", lambda *a, **k: True
    elif boundary == "result-ack":
        target, method, predicate = harness.stores["ownership"], "acknowledge_result", lambda *a, **k: True
    elif boundary == "completion-event":
        target, method = harness.stores["events"], "append_fenced"

        def predicate(event, **kwargs):
            return event.event_type == "workflow.step.completed" and event.step_id == "work"
    else:
        target, method = harness.stores["checkpoints"], "save_fenced"

        def predicate(checkpoint, **kwargs):
            return checkpoint.task_id == request.control_task_id and "after" in checkpoint.state.runtime_metadata.get(
                "running", {}
            )

    crash_once(monkeypatch, target, method, after=after, predicate=predicate)
    with pytest.raises(SimulatedHubCrash):
        harness.hub.advance(request)
    harness.restart()
    result = harness.finish(request)
    assert result.status == "completed", result.reason_code
    assert harness.handler.calls == ["work", "after"]
    assert result.checkpoint.state.runtime_metadata["budget_usage"] == {"tokens": 2, "cost_micros": 2}


@pytest.mark.parametrize("after", [False, True], ids=["before", "after"])
def test_cancel_crash_keeps_successor_ineligible(harness, monkeypatch, after):
    request = linear_request()
    running = harness.dispatch_first(request)
    command = signed_control(
        keys=harness.keys,
        checkpoint=running.checkpoint,
        command_type="cancel",
        step_id="work",
        command_id="synthetic-cancel",
    )
    crash_once(
        monkeypatch,
        harness.stores["checkpoints"],
        "save_fenced",
        after=after,
        predicate=lambda checkpoint, **kwargs: checkpoint.task_id == request.control_task_id,
    )
    with pytest.raises(SimulatedHubCrash):
        harness.hub.resume(request, command=command)
    harness.restart()
    cancelled = harness.hub.resume(request, command=command, admitted_replay=True)
    assert cancelled.status == "cancelled"
    assert harness.hub.advance(request).status == "cancelled"
    assert harness.handler.calls == ["work"]


def test_acknowledged_result_cannot_change_content_during_crash_replay(harness, monkeypatch):
    from dataclasses import replace

    request = linear_request()
    harness.dispatch_first(request)
    crash_once(monkeypatch, harness.stores["ownership"], "acknowledge_result", after=True)
    with pytest.raises(SimulatedHubCrash):
        harness.hub.advance(request)
    original = harness.queue.results["hub-task-1"]
    harness.queue.results["hub-task-1"] = replace(original, output_data={"value": "changed-after-ack"})
    harness.restart()
    try:
        result = harness.finish(request)
    except (ValueError, RuntimeError):
        result = harness.hub.inspect(request)
    if "work" in result.completed_node_ids:
        assert result.checkpoint.state.business_data["node_results"]["work"] == original.output_data, (
            "Changed bytes under an acknowledged result ID replaced the accepted result"
        )
    else:
        assert "after" not in harness.handler.calls, "A rejected result authorized a successor"


@pytest.mark.parametrize("boundary", ["queue", "delegated-event"])
def test_cancel_adopts_committed_task_missing_from_checkpoint(harness, monkeypatch, boundary):
    request = linear_request()
    harness.hub.start(request)
    target, method = (
        (harness.queue, "submit_fenced") if boundary == "queue" else (harness.stores["events"], "append_fenced")
    )

    def predicate(*args, **kwargs):
        return boundary == "queue" or args[0].event_type == "workflow.step.delegated"

    crash_once(monkeypatch, target, method, after=True, predicate=predicate)
    with pytest.raises(SimulatedHubCrash):
        harness.hub.advance(request)
    assert harness.queue.submissions and not harness.hub.checkpoint(request).state.runtime_metadata["running"]
    harness.restart()
    command = signed_control(
        keys=harness.keys,
        checkpoint=harness.hub.checkpoint(request),
        command_type="cancel",
        step_id="work",
        command_id="cancel-orphan",
    )
    result = harness.hub.resume(request, command=command)
    assert result.status == "cancelled"
    assert harness.queue.cancelled == ["hub-task-1"]
    assert not harness.grants.revalidate(harness.queue.submissions[0].authorization)
    assert harness.handler.calls == ["work"]


def test_crash_after_gateway_event_reuses_immutable_selection(harness, monkeypatch):
    from agent.services.native_graph_orchestration_service import NativeGraphRequest
    from tests.bpmn.test_execution_routing import compile_request, execution_plan, xor_xml

    request = NativeGraphRequest(
        execution_plan(compile_request(xor_xml())), "synthetic-gateway-crash", "control", input_data={"approved": True}
    )
    first = harness.hub.start(request)
    crash_once(
        monkeypatch,
        harness.stores["events"],
        "append_fenced",
        after=True,
        predicate=lambda event, **kwargs: event.step_id == "choose" and event.event_type == "workflow.step.completed",
    )
    with pytest.raises(SimulatedHubCrash):
        harness.hub.advance(request)
    harness.restart()
    result = advance_bounded(harness.hub, request, first)
    assert result.status == "completed"
    assert harness.handler.calls == ["yes_task"]
    selected = [event for event in harness.hub.stream(request) if event.step_id == "choose"]
    assert len(selected) == 1
    assert selected[0].payload["selected_edge"] == "yes"


@pytest.mark.parametrize("boundary", ["queue", "event", "checkpoint"])
def test_expired_operation_cannot_commit_after_successor_lease_acquisition(harness, monkeypatch, boundary):
    """Pause after caller's last lease check, at the mutation recipient."""
    from agent.services.bpmn_run_lease import BpmnRunLeaseService
    from agent.services.workflow_runtime.errors import WorkflowRuntimeError
    from agent.services.workflow_runtime.persistence import SQLiteCheckpointStore

    request = linear_request()
    harness.hub.start(request)
    successor_store = SQLiteCheckpointStore(harness.directory / "checkpoints.sqlite")
    harness.connections.append(successor_store)
    successor_leases = BpmnRunLeaseService(checkpoints=successor_store, keys=harness.keys, clock=lambda: harness.now)
    target, method = {
        "queue": (harness.queue, "submit_fenced"),
        "event": (harness.stores["events"], "append_fenced"),
        "checkpoint": (harness.stores["checkpoints"], "save_fenced"),
    }[boundary]
    operation = getattr(target, method)
    injected = False
    before_mutation = []

    def recipient(*args, **kwargs):
        nonlocal injected
        selected = (
            boundary == "queue"
            or (boundary == "event" and args[0].event_type == "workflow.step.delegated")
            or (
                boundary == "checkpoint"
                and args[0].task_id == request.control_task_id
                and "work" in args[0].state.runtime_metadata.get("running", {})
            )
        )
        if injected or not selected:
            return operation(*args, **kwargs)
        injected = True
        before_mutation.append(harness.hub.stream(request) if boundary == "event" else harness.hub.checkpoint(request))
        harness.now = 131.0  # Exactly bounded operation expiry; workflow deadline is later.
        with successor_leases.acquire(request) as successor:
            successor.ensure_valid()
            return operation(*args, **kwargs)

    monkeypatch.setattr(target, method, recipient)
    try:
        harness.hub.advance(request)
    except WorkflowRuntimeError:
        pass
    assert injected, "The intended mutation boundary was not reached"
    if boundary == "queue":
        assert harness.handler.calls == [], "Stale control owner executed work after a successor acquired the lease"
    elif boundary == "event":
        assert harness.hub.stream(request) == before_mutation[0], (
            "Stale owner appended a delegation event after takeover"
        )
    else:
        assert harness.hub.checkpoint(request) == before_mutation[0], "Stale owner committed a Native checkpoint"


def test_expired_queue_admission_cannot_dispatch_after_other_hub_cancels(harness, monkeypatch):
    from agent.services.workflow_runtime.errors import WorkflowRuntimeError

    request = linear_request()
    harness.hub.start(request)
    stale_hub = harness.hub
    submit = harness.queue.submit_fenced
    cancelled = []

    def delayed_admission(command, *, lease):
        harness.now = 131.0
        successor = harness.restart()
        cancel = signed_control(
            keys=harness.keys,
            checkpoint=successor.checkpoint(request),
            command_type="cancel",
            step_id="work",
            command_id="synthetic-takeover-cancel",
        )
        cancelled.append(successor.resume(request, command=cancel))
        return submit(command, lease=lease)

    monkeypatch.setattr(harness.queue, "submit_fenced", delayed_admission)
    try:
        stale_hub.advance(request)
    except WorkflowRuntimeError:
        pass
    assert cancelled and cancelled[0].status == "cancelled"
    assert harness.hub.inspect(request).status == "cancelled"
    assert harness.handler.calls == [], "Queue admitted the expired owner's task after cancellation committed"


def test_process_exit_leaves_lease_until_bounded_takeover(harness):
    """Actual child exit (not exception unwinding) leaves a durable lease."""
    import multiprocessing
    import os

    from agent.services.bpmn_run_lease import BpmnRunLeaseService
    from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
    from agent.services.workflow_runtime.persistence import SQLiteCheckpointStore

    request = linear_request()
    initial = harness.hub.start(request)

    def acquire_and_exit():
        store = SQLiteCheckpointStore(harness.directory / "checkpoints.sqlite")
        lease = BpmnRunLeaseService(checkpoints=store, keys=harness.keys, clock=lambda: 100.0)
        with lease.acquire(request):
            os._exit(73)

    child = multiprocessing.get_context("fork").Process(target=acquire_and_exit)
    child.start()
    child.join(timeout=5)
    if child.is_alive():
        child.terminate()
        child.join(timeout=2)
        pytest.fail("Synthetic crash subprocess exceeded its five-second bound")
    assert child.exitcode == 73
    with pytest.raises(OptimisticConcurrencyError, match="lease_held"):
        harness.hub.advance(request)
    assert harness.hub.checkpoint(request) == initial.checkpoint
    assert harness.queue.submissions == []
    harness.now = 130.0
    harness.restart()
    assert harness.finish(request).status == "completed"
    assert harness.handler.calls == ["work", "after"]


def test_operation_expiring_during_poll_cannot_acknowledge_result(harness, monkeypatch):
    from agent.services.workflow_runtime.errors import WorkflowRuntimeError

    request = linear_request()
    running = harness.dispatch_first(request)
    poll = harness.queue.poll

    def delayed_poll(**kwargs):
        harness.now = 131.0
        return poll(**kwargs)

    monkeypatch.setattr(harness.queue, "poll", delayed_poll)
    with pytest.raises(WorkflowRuntimeError, match="lease_stale"):
        harness.hub.advance(request)
    ownership = harness.stores["ownership"].get(tenant_id="tenant-a", run_id=request.run_id, step_id="work")
    assert ownership.status == "active", "Expired operation acknowledged a result before checking its lease"
    assert harness.hub.checkpoint(request) == running.checkpoint
    assert harness.handler.calls == ["work"]


@pytest.mark.parametrize("mutation", [None, "node", "input", "plan", "tenant", "run", "revoked-grant"])
def test_real_queue_submission_reader_and_sql_grant_bind_recovery(harness, monkeypatch, mutation):
    """Actual adapter/repository and recovery service over a stored TaskDB row.

    This targets persisted command recovery only; task ingestion/dispatch is
    outside this component test and is not represented as full-stack coverage.
    """
    from dataclasses import replace

    from sqlmodel import Session, SQLModel

    from agent.db_models.tasks import TaskDB
    from agent.repositories import tasks
    from agent.services.native_graph_task_queue_adapter import AnantaHubTaskQueueAdapter, _task_id
    from agent.services.native_submission_recovery import recover_submission

    request = linear_request()
    harness.dispatch_first(request)
    original = harness.queue.submissions[0]
    command = original
    if mutation == "node":
        command = replace(command, node=replace(command.node, allowed_tools=("shell.execute",)))
    elif mutation == "input":
        command = replace(command, input_data={"changed": True})
    elif mutation == "plan":
        command = replace(command, plan_hash="0" * 64)
    elif mutation == "tenant":
        command = replace(command, tenant_id="foreign")
    elif mutation == "run":
        command = replace(command, run_id="foreign")
    elif mutation == "revoked-grant":
        harness.grants.revoke(command.authorization.envelope_id, reason_code="synthetic-revocation")

    database = harness.grant_database
    SQLModel.metadata.create_all(database, tables=[TaskDB.__table__])
    task_id = _task_id(original.command_id)
    with Session(database) as session:
        session.add(
            TaskDB(
                id=task_id,
                tenant_id=original.tenant_id,
                status="created",
                created_at=100.0,
                updated_at=100.0,
                worker_execution_context={
                    "schema": "ananta.native_graph_worker_context.v1",
                    "runtime_path": "native_graph_node",
                    "native_node_command": command.to_dict(),
                },
            )
        )
        session.commit()
    monkeypatch.setattr(tasks, "_engine", lambda: database)
    adapter = AnantaHubTaskQueueAdapter(task_queue=None, task_repository=tasks.TaskRepository(), task_runtime=None)
    owner = harness.stores["ownership"].get(tenant_id=original.tenant_id, run_id=original.run_id, step_id="work")
    arguments = dict(
        queue=adapter,
        grants=harness.grants,
        plan=request.plan,
        request=request,
        node=original.node,
        ownership=owner,
        input_data=original.input_data,
    )
    if mutation:
        with pytest.raises((ValueError, PermissionError), match="binding_mismatch|grant_denied"):
            recover_submission(**arguments)
    else:
        first = recover_submission(**arguments)
        second = recover_submission(**arguments)
        assert first == second
        assert first.command == original
        assert first.receipt.hub_task_id == task_id
        assert len(tasks.TaskRepository().get_all()) == 1
    assert harness.handler.calls == ["work"]
