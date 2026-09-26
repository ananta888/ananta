"""Synthetic contract/Hub runtime checks, not production release evidence."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.workflow_backend import WorkflowRequest
from agent.services.workflow_runtime.execution_plan import WorkflowRequestExecutionPlanAdapter
from agent.visual_process.blueprint_mapper import graph_to_workflow_request
from agent.visual_process.bpmn_adapter import import_bpmn_xml
from tests.bpmn.test_execution_admission import diagram
from tests.test_native_graph_runtime import runtime


def xor_xml(*, second="input.approved == False", default=False):
    fallback = ' default="no"' if default else ""
    second_condition = "" if default else f"<conditionExpression>{second}</conditionExpression>"
    return diagram(
        f'<startEvent id="start"/><exclusiveGateway id="choose"{fallback}/>'
        '<serviceTask id="yes_task"/><serviceTask id="no_task"/><serviceTask id="no_child"/>'
        '<exclusiveGateway id="join"/><endEvent id="end"/>'
        '<sequenceFlow id="begin" sourceRef="start" targetRef="choose"/>'
        '<sequenceFlow id="yes" sourceRef="choose" targetRef="yes_task">'
        "<conditionExpression>input.approved == True</conditionExpression></sequenceFlow>"
        f'<sequenceFlow id="no" sourceRef="choose" targetRef="no_task">{second_condition}</sequenceFlow>'
        '<sequenceFlow id="child" sourceRef="no_task" targetRef="no_child"/>'
        '<sequenceFlow id="yes_join" sourceRef="yes_task" targetRef="join"/>'
        '<sequenceFlow id="no_join" sourceRef="no_child" targetRef="join"/>'
        '<sequenceFlow id="finish" sourceRef="join" targetRef="end"/>'
    )


def compile_request(xml):
    graph = import_bpmn_xml(xml).graph
    return graph_to_workflow_request(graph, policy_scope={"source": "synthetic-test"})


def execution_plan(request):
    return WorkflowRequestExecutionPlanAdapter.adapt(request, tenant_id="tenant-a", policy_version="policy-v1")


@pytest.mark.parametrize("approved,expected", [(True, ["yes_task"]), (False, ["no_task", "no_child"])])
def test_xor_selects_only_one_branch_and_propagates_skips(approved, expected):
    request = compile_request(xor_xml())
    restored = WorkflowRequest.from_mapping(request.to_dict())
    assert restored.validate() == []
    plan = execution_plan(restored)
    assert plan.validate() == ()
    hub, queue, handler, *_ = runtime()
    assignment = NativeGraphRequest(plan, "synthetic-bpmn-run", "control-bpmn", input_data={"approved": approved})
    result = hub.start(assignment)
    for _ in range(20):
        if result.status in {"completed", "failed", "cancelled"}:
            break
        result = hub.advance(assignment)
    assert result.status == "completed", result.reason_code
    assert handler.calls == expected
    assert [command.node.node_id for command in queue.submissions] == expected


@pytest.mark.parametrize("default,approved,expected", [(True, False, "no"), (False, True, "yes")])
def test_xor_default_and_stable_first_match(default, approved, expected):
    from agent.services.bpmn_control_nodes import decide_control

    request = compile_request(xor_xml(second="input.approved == True", default=default))
    decision = decide_control(request.execution_graph["controls"]["choose"], {"input": {"approved": approved}})
    assert decision["selected_edge"] == expected


@pytest.mark.parametrize(
    "input_data,reason",
    [
        ({}, "condition_field_unknown"),
        ({"approved": "yes"}, "condition_type_mismatch"),
        ({"approved": False}, "no_matching_flow"),
    ],
)
def test_invalid_or_unmatched_gateway_fails_without_branch_delegation(input_data, reason):
    plan = execution_plan(compile_request(xor_xml(second="input.approved == True")))
    hub, queue, *_ = runtime()
    assignment = NativeGraphRequest(plan, "synthetic-bpmn-run", "control-bpmn", input_data=input_data)
    result = hub.start(assignment)
    for _ in range(5):
        if result.status != "running":
            break
        result = hub.advance(assignment)
    assert result.status == "failed"
    assert reason in result.reason_code
    assert queue.submissions == []


def test_request_cannot_drop_or_replace_compiled_conditions():
    request = compile_request(xor_xml())
    assert "bpmn_execution_graph_required" in replace(request, execution_graph=None).validate()
    raw = request.to_dict()
    raw["execution_graph"]["edges"][1]["condition"] = {"op": "always"}
    assert "bpmn_execution_graph_mismatch" in WorkflowRequest.from_mapping(raw).validate()


def test_compiler_rejects_condition_change_without_updated_xml():
    graph = import_bpmn_xml(xor_xml()).graph
    graph.edges[1].condition.expression = "True"
    with pytest.raises(ValueError, match="binding_mismatch"):
        graph_to_workflow_request(graph, policy_scope={"source": "synthetic-test"})


@pytest.mark.parametrize(
    "expression", ['__import__("os").getcwd()', "input.approved()", 'input["approved"]', "input.approved + 1"]
)
def test_expressions_are_not_executable_code(expression):
    from agent.visual_process.bpmn_conditions import compile_condition

    with pytest.raises(ValueError, match="expression_unsupported"):
        compile_condition(expression, element_id="edge")


def test_legacy_dependency_only_request_still_adapts():
    request = WorkflowRequest.from_mapping(
        {"workflow_id": "legacy", "steps": [{"id": "a"}], "policy_scope": {"source": "test"}}
    )
    assert request.validate() == []
    assert execution_plan(request).nodes[0].node_type == "task"


def test_edge_ids_conditions_and_definition_hash_survive_plan_wire_roundtrip():
    from jsonschema import validate

    from agent.services.workflow_runtime.execution_plan import EXECUTION_PLAN_JSON_SCHEMA, ExecutionPlan

    plan = execution_plan(compile_request(xor_xml()))
    payload = plan.to_dict()
    validate(payload, EXECUTION_PLAN_JSON_SCHEMA)
    restored = ExecutionPlan.from_mapping(payload)
    assert restored.plan_hash == plan.plan_hash
    assert restored.to_dict() == payload
    assert all(edge.edge_id for edge in restored.edges)
    changed = execution_plan(compile_request(xor_xml(second="input.approved != True")))
    assert changed.metadata["bpmn_definition_hash"] != plan.metadata["bpmn_definition_hash"]
    assert changed.plan_hash != plan.plan_hash
