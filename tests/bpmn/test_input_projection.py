"""Synthetic scope boundaries, independent of XML, workers and orchestration."""

import pytest

from agent.services.bpmn_input_projection import (
    PROJECTION_SCHEMA,
    project_bpmn_inputs,
    projection_result_dependencies,
    validate_input_projection,
)


def projection(inputs=None, results=None):
    return {"schema": PROJECTION_SCHEMA, "workflow_input": inputs or {}, "dependency_results": results or {}}


def test_explicit_scope_does_not_leak_ambient_input_or_other_results():
    context = {"permitted": {"value": 3}, "private": "not in child"}
    results = {"parent": {"export": {"done": True}, "internal": "not in child"}, "unrelated": {"x": 9}}
    spec = projection({"arg": ["input", "permitted"]}, {"public": ["results", "parent", "export"]})
    projected = project_bpmn_inputs(spec, input_data=context, results=results)
    assert projected == {"workflow_input": {"arg": {"value": 3}}, "dependency_results": {"public": {"done": True}}}
    projected["workflow_input"]["arg"]["value"] = 5
    assert context["permitted"]["value"] == 3
    assert projection_result_dependencies(spec) == {"parent"}


def test_empty_projection_is_empty_instead_of_legacy_fallback():
    assert project_bpmn_inputs(projection(), input_data={"private": 1}, results={"parent": 2}) == {
        "workflow_input": {},
        "dependency_results": {},
    }


@pytest.mark.parametrize("path", [["input"], ["secrets", "x"], ["input", 0], ["input", "constructor"], "input.x"])
def test_invalid_paths_are_not_programs_or_wildcard_authority(path):
    assert validate_input_projection(projection({"arg": path}))


@pytest.mark.parametrize("data", [{}, {"items": [1]}, {"items": None}])
def test_missing_or_non_dictionary_traversal_is_fail_closed(data):
    with pytest.raises(ValueError, match="field_missing"):
        project_bpmn_inputs(projection({"arg": ["input", "items", "0"]}), input_data=data, results={})


def test_bounded_cyclic_or_large_results():
    cycle = {}
    cycle["cycle"] = cycle
    for value in (cycle, "x" * 65536, list(range(5000))):
        with pytest.raises(ValueError, match="value_limit"):
            project_bpmn_inputs(projection({"arg": ["input", "selected"]}), input_data={"selected": value}, results={})


def test_native_delegation_receives_only_the_compiled_child_view():
    from dataclasses import replace

    from agent.services.native_graph_orchestration_service import NativeGraphRequest
    from tests.bpmn.test_execution_admission import diagram
    from tests.bpmn.test_execution_recovery import advance_bounded
    from tests.bpmn.test_execution_routing import compile_request, execution_plan
    from tests.test_native_graph_runtime import runtime

    plan = execution_plan(
        compile_request(
            diagram(
                '<startEvent id="s"/><task id="work"/><endEvent id="e"/>'
                '<sequenceFlow id="a" sourceRef="s" targetRef="work"/>'
                '<sequenceFlow id="b" sourceRef="work" targetRef="e"/>'
            )
        )
    )
    plan = replace(
        plan,
        capabilities=tuple(sorted({*plan.capabilities, "bpmn_activation_v1"})),
        nodes=tuple(
            replace(
                node, metadata={**node.metadata, "bpmn_input_projection": projection({"selected": ["input", "public"]})}
            )
            if node.node_id == "work"
            else node
            for node in plan.nodes
        ),
    )
    hub, queue, *_ = runtime()
    request = NativeGraphRequest(plan, "synthetic-scoped", "control", input_data={"public": 2, "private": 7})
    result = advance_bounded(hub, request, hub.start(request))
    assert result.status == "completed", result.reason_code
    assert queue.submissions[0].input_data == {
        "workflow_input": {"selected": 2},
        "dependency_results": {},
        "requested_artifacts": [],
    }


def test_plan_rejects_forward_results_and_missing_scope_capability():
    from dataclasses import replace

    from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan

    node = ExecutionNode("child", metadata={"bpmn_input_projection": projection({"x": ["results", "future"]})})
    plan = ExecutionPlan(tenant_id="tenant", workflow_id="flow", plan_id="plan", policy_version="policy", nodes=(node,))
    codes = {issue.code for issue in plan.validate()}
    assert "bpmn_activation_capability_required" in codes
    assert "bpmn_input_projection_dependency_unbound" in codes
    assert "bpmn_input_projection_dependency_unbound" in {
        issue.code for issue in replace(plan, capabilities=("bpmn_activation_v1",)).validate()
    }
