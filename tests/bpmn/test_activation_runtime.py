"""Exercise preview DAGs in the existing synthetic Hub/Worker harness only."""

from dataclasses import replace

import pytest

from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.workflow_runtime.execution_plan import ExecutionEdge, ExecutionNode
from agent.visual_process.bpmn_activation_contracts import ORIGIN_KEY
from tests.bpmn.test_activation_compilation import (
    activation,
    compile_definition,
    definition,
    loop_definition,
    register,
)
from tests.bpmn.test_execution_recovery import advance_bounded, restart
from tests.test_native_graph_runtime import runtime, signed_control


def loop_runtime(monkeypatch, *, enter=True, iterations=1, maximum=3):
    registry, pin, _ = loop_definition(maximum)
    candidate = compile_definition(registry, pin).candidate_plan
    hub, queue, handler, keys, ledger, stores = runtime()
    execute = handler.execute

    def result(command, *, hub_task_id):
        value = execute(command, hub_task_id=hub_task_id)
        iteration = command.node.metadata[ORIGIN_KEY]["path"][-1]["iteration"]
        return replace(value, output_data={"again": iteration + 1 < iterations})

    monkeypatch.setattr(handler, "execute", result)
    request = NativeGraphRequest(candidate, "synthetic-loop", "control", input_data={"enter": enter})
    return hub, queue, handler, keys, ledger, stores, request


@pytest.mark.parametrize(
    "enter,iterations,maximum,expected", [(False, 1, 0, 0), (False, 1, 3, 0), (True, 1, 3, 1), (True, 3, 3, 3)]
)
def test_zero_one_and_multiple_iterations_use_distinct_hub_assignments(
    monkeypatch, enter, iterations, maximum, expected
):
    hub, queue, handler, *_, request = loop_runtime(monkeypatch, enter=enter, iterations=iterations, maximum=maximum)
    result = advance_bounded(hub, request, hub.start(request))
    assert result.status == "completed", result.reason_code
    assert len(handler.calls) == len(queue.submissions) == expected
    assert len({command.node.node_id for command in queue.submissions}) == expected
    assert len({command.command_id for command in queue.submissions}) == expected
    assert all(command.node.node_type == "task" for command in queue.submissions)


@pytest.mark.parametrize("maximum", [0, 2])
def test_still_true_condition_at_limit_fails_instead_of_reporting_success(monkeypatch, maximum):
    hub, queue, handler, *_, request = loop_runtime(monkeypatch, iterations=100, maximum=maximum)
    result = advance_bounded(hub, request, hub.start(request))
    assert result.status == "failed"
    assert result.reason_code == "bpmn_gateway_no_matching_flow"
    assert len(handler.calls) == maximum


def test_unknown_entry_condition_never_delegates(monkeypatch):
    hub, queue, handler, *_, request = loop_runtime(monkeypatch)
    request = replace(request, input_data={})
    result = advance_bounded(hub, request, hub.start(request))
    assert result.status == "failed"
    assert "condition_not_unknown" in result.reason_code
    assert queue.submissions == []


def test_restart_within_iteration_reuses_frozen_plan_without_resolving_registry(monkeypatch):
    hub, queue, handler, keys, ledger, stores, request = loop_runtime(monkeypatch, iterations=3)
    result = hub.start(request)
    for _ in range(5):
        if queue.submissions:
            break
        result = hub.advance(request)
    assert len(queue.submissions) == 1
    hub = restart(queue, keys, ledger, stores)
    result = advance_bounded(hub, request, result)
    assert result.status == "completed", result.reason_code
    assert len(queue.submissions) == len(set(handler.calls)) == 3
    assert hub.advance(request).status == "completed"
    assert len(queue.submissions) == 3


def test_prior_iteration_result_cannot_complete_current_assignment(monkeypatch):
    hub, queue, handler, *_, request = loop_runtime(monkeypatch, iterations=3)
    result = hub.start(request)
    for _ in range(8):
        if queue.results:
            break
        result = hub.advance(request)
    stale = next(iter(queue.results.values()))
    for _ in range(8):
        if len(queue.submissions) == 2:
            break
        result = hub.advance(request)
    assert len(queue.submissions) == 2
    current_task, current = next(iter(queue.results.items()))
    queue.results[current_task] = replace(stale, node_id=current.node_id)
    with pytest.raises(ValueError, match="result_binding_mismatch"):
        hub.advance(request)
    assert len(queue.submissions) == 2
    queue.results[current_task] = current
    result = advance_bounded(hub, request, hub.advance(request))
    assert result.status == "completed"
    assert len(queue.submissions) == 3


def test_cancel_stops_current_iteration_and_never_starts_the_next(monkeypatch):
    hub, queue, handler, keys, *_, request = loop_runtime(monkeypatch, iterations=3)
    result = hub.start(request)
    for _ in range(5):
        if queue.submissions:
            break
        result = hub.advance(request)
    result = hub.resume(
        request,
        command=signed_control(
            keys=keys,
            checkpoint=result.checkpoint,
            command_type="cancel",
            step_id="__workflow__",
            command_id="cancel-loop",
        ),
    )
    assert result.status == "cancelled"
    assert len(queue.cancelled) == 1
    assert hub.advance(request).status == "cancelled"
    assert len(queue.submissions) == 1


def test_pinned_subprocess_restart_and_parent_cancellation_use_existing_hub_ports():
    from agent.services.workflow_runtime.components import WorkflowComponentRegistry

    registry = WorkflowComponentRegistry()
    body = register(
        registry,
        definition("child", [ExecutionNode("a"), ExecutionNode("b")], [ExecutionEdge("a", "b", edge_id="inside")]),
    )
    root = register(
        registry,
        definition(
            "root",
            [activation("call", body), ExecutionNode("after")],
            [ExecutionEdge("call", "after", edge_id="outside")],
        ),
    )
    candidate = compile_definition(registry, root).candidate_plan
    hub, queue, handler, keys, ledger, stores = runtime()
    request = NativeGraphRequest(candidate, "synthetic-call", "control")
    result = hub.start(request)
    assert len(queue.submissions) == 1
    hub = restart(queue, keys, ledger, stores)
    result = hub.advance(request)
    assert len(queue.submissions) == 2
    result = hub.resume(
        request,
        command=signed_control(
            keys=keys,
            checkpoint=result.checkpoint,
            command_type="cancel",
            step_id="__workflow__",
            command_id="cancel-call",
        ),
    )
    assert result.status == "cancelled"
    assert len(queue.cancelled) == 1
    assert len(queue.submissions) == 2


def test_xor_in_repeated_body_rebinds_ids_targets_and_selected_edge_values(monkeypatch):
    from agent.services.workflow_runtime.components import WorkflowComponent, WorkflowComponentRegistry
    from tests.bpmn.test_activation_compilation import BUDGET, loop_options
    from tests.bpmn.test_execution_routing import compile_request, execution_plan, xor_xml

    body_plan = replace(execution_plan(compile_request(xor_xml())), budget=BUDGET)
    # Core source graph nodes inherit the plan budget here.
    body_plan = replace(body_plan, nodes=tuple(replace(node, budget=None) for node in body_plan.nodes))
    body = WorkflowComponent("xor_body", "1.0.0", "policy-v1", body_plan)
    registry = WorkflowComponentRegistry()
    pin = register(registry, body)
    options = loop_options(2)
    options["repeat_condition"] = {"op": "eq", "field": "input.repeat", "value": True}
    root = register(registry, definition("root", [activation("loop", pin, kind="while", **options)]))
    candidate = compile_definition(registry, root).candidate_plan
    hub, queue, handler, *_ = runtime()
    request = NativeGraphRequest(
        candidate, "synthetic-xor-loop", "control", input_data={"enter": True, "approved": True, "repeat": False}
    )
    result = advance_bounded(hub, request, hub.start(request))
    assert result.status == "completed", result.reason_code
    assert [command.node.metadata[ORIGIN_KEY]["source_id"] for command in queue.submissions] == ["yes_task"]
