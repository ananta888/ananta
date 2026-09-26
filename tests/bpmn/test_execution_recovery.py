"""Single-activation DAG checks with synthetic worker results and shared stores."""

from dataclasses import replace

import pytest

from agent.services.native_graph_orchestration_service import NativeGraphOrchestrator, NativeGraphRequest
from agent.services.workflow_runtime import InMemoryReplayNonceStore, WorkflowCommandVerifier
from tests.bpmn.test_execution_admission import diagram
from tests.bpmn.test_execution_routing import compile_request, execution_plan, xor_xml
from tests.test_native_graph_runtime import AllowPolicy, runtime


def restart(queue, keys, ledger, stores, *, clock=lambda: 100.0):
    return NativeGraphOrchestrator(
        queue=queue,
        key_ring=keys,
        ledger=ledger,
        policy=AllowPolicy(),
        clock=clock,
        checkpoints=stores["checkpoints"],
        events=stores["events"],
        ownership=stores["ownership"],
        command_verifier=WorkflowCommandVerifier(keys, InMemoryReplayNonceStore(clock=lambda: 100.0)),
    )


def advance_bounded(hub, request, result):
    for _ in range(30):
        if result.status in {"completed", "failed", "cancelled", "waiting_for_approval"}:
            return result
        result = hub.advance(request)
    pytest.fail("Run exceeded deterministic step bound")


def parallel_xml():
    nodes = [
        ("startEvent", "s"),
        ("parallelGateway", "split"),
        ("serviceTask", "a"),
        ("parallelGateway", "nested"),
        ("serviceTask", "b"),
        ("serviceTask", "c"),
        ("parallelGateway", "nested_join"),
        ("parallelGateway", "join"),
        ("serviceTask", "after"),
        ("endEvent", "end"),
    ]
    links = [
        ("s", "split"),
        ("split", "a"),
        ("split", "nested"),
        ("nested", "b"),
        ("nested", "c"),
        ("b", "nested_join"),
        ("c", "nested_join"),
        ("a", "join"),
        ("nested_join", "join"),
        ("join", "after"),
        ("after", "end"),
    ]
    return diagram(
        "".join(f'<{kind} id="{identity}"/>' for kind, identity in nodes)
        + "".join(
            f'<sequenceFlow id="f{i}" sourceRef="{source}" targetRef="{target}"/>'
            for i, (source, target) in enumerate(links)
        )
    )


def test_nested_parallel_join_restarts_without_second_delegation(monkeypatch):
    hub, queue, handler, keys, ledger, stores = runtime()
    original_poll = queue.poll
    monkeypatch.setattr(queue, "poll", lambda **values: tuple(reversed(original_poll(**values))))
    request = NativeGraphRequest(
        execution_plan(compile_request(parallel_xml())), "parallel-test", "control", tenant_parallel_limit=3
    )
    result = hub.start(request)
    for _ in range(5):
        result = hub.advance(request)
    hub = restart(queue, keys, ledger, stores)
    result = advance_bounded(hub, request, result)
    assert result.status == "completed", result.reason_code
    assert sorted(handler.calls) == ["a", "after", "b", "c"]
    assert handler.calls[-1] == "after"
    assert len(queue.submissions) == 4
    assert hub.advance(request).status == "completed"
    assert len(queue.submissions) == 4


def test_xor_restart_reuses_bound_input_and_persisted_decision():
    hub, queue, handler, keys, ledger, stores = runtime()
    request = NativeGraphRequest(
        execution_plan(compile_request(xor_xml())), "xor-restart", "control", input_data={"approved": True}
    )
    result = hub.start(request)
    result = hub.advance(request)
    hub = restart(queue, keys, ledger, stores)
    with pytest.raises(ValueError, match="input_binding_mismatch"):
        hub.advance(replace(request, input_data={"approved": False}))
    result = advance_bounded(hub, request, result)
    assert result.status == "completed"
    assert handler.calls == ["yes_task"]


def test_parallel_join_cannot_succeed_from_only_one_exclusive_branch():
    xml = xor_xml().replace('<exclusiveGateway id="join"/>', '<parallelGateway id="join"/>')
    hub, queue, handler, *_ = runtime()
    request = NativeGraphRequest(
        execution_plan(compile_request(xml)), "invalid-join", "control", input_data={"approved": True}
    )
    result = advance_bounded(hub, request, hub.start(request))
    assert result.status == "failed"
    assert result.reason_code == "bpmn_parallel_incomplete_activation"
    assert handler.calls == ["yes_task"]


def test_worker_failure_does_not_advance_parallel_join():
    hub, queue, handler, *_ = runtime(fail_once={"b"})
    request = NativeGraphRequest(execution_plan(compile_request(parallel_xml())), "failed-branch", "control")
    result = advance_bounded(hub, request, hub.start(request))
    assert result.status == "failed"
    assert "after" not in handler.calls


def test_run_deadline_survives_restart_and_prevents_any_late_delegation():
    hub, queue, handler, keys, ledger, stores = runtime()
    plan = execution_plan(compile_request(xor_xml()))
    plan = replace(plan, budget=replace(plan.budget, timeout_seconds=10.0))
    request = NativeGraphRequest(plan, "deadline-test", "control", input_data={"approved": True})
    first = hub.start(request)
    assert first.checkpoint.state.runtime_metadata["bpmn_deadline_at"] == 110
    resumed = restart(queue, keys, ledger, stores, clock=lambda: 111.0).advance(request)
    assert resumed.status == "failed"
    assert resumed.reason_code == "bpmn_run_deadline_exceeded"
    assert queue.submissions == []


def test_active_bpmn_definition_cannot_be_replaced_after_start():
    from agent.services.native_graph_models import NativeRunState

    plan = execution_plan(compile_request(xor_xml()))
    replacement = replace(plan, metadata={**plan.metadata, "bpmn_definition_hash": "different"})
    with pytest.raises(ValueError, match="bpmn_running_definition_immutable"):
        NativeGraphOrchestrator._assert_safe_plan_edit(plan, replacement, NativeRunState())


def test_expired_approval_cannot_revive_a_bpmn_run():
    from tests.test_native_graph_runtime import signed_control

    xml = diagram(
        '<startEvent id="s"/><userTask id="review"/><endEvent id="end"/>'
        '<sequenceFlow id="a" sourceRef="s" targetRef="review"/>'
        '<sequenceFlow id="b" sourceRef="review" targetRef="end"/>'
    )
    hub, queue, handler, keys, ledger, stores = runtime()
    plan = execution_plan(compile_request(xml))
    plan = replace(plan, budget=replace(plan.budget, timeout_seconds=10.0))
    request = NativeGraphRequest(plan, "expired-gate-test", "control")
    waiting = advance_bounded(hub, request, hub.start(request))
    assert waiting.status == "waiting_for_approval"
    command = signed_control(
        keys=keys,
        checkpoint=waiting.checkpoint,
        command_type="approve",
        step_id="review",
        command_id="too-late-policy-decision",
    )
    restarted = restart(queue, keys, ledger, stores, clock=lambda: 111.0)
    result = restarted.resume(request, command=command)
    assert result.status == "failed"
    assert result.reason_code == "bpmn_run_deadline_exceeded"
    assert queue.submissions == []
    assert result.checkpoint.state.runtime_metadata["approved_gates"] == []
