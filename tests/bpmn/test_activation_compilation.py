"""Synthetic pre-admission expansion contracts; no production evidence."""

from copy import deepcopy
from dataclasses import replace

import pytest

from agent.services.workflow_runtime.components import WorkflowComponent, WorkflowComponentRegistry
from agent.services.workflow_runtime.execution_plan import ExecutionBudget, ExecutionEdge, ExecutionNode, ExecutionPlan
from agent.visual_process.bpmn_activation import BpmnActivationCompiler
from agent.visual_process.bpmn_activation_contracts import (
    ACTIVATION_KEY,
    ORIGIN_KEY,
    ActivationLimits,
    BpmnActivationError,
    DefinitionPin,
    component_sha256,
)

BUDGET = ExecutionBudget(timeout_seconds=20, max_tokens=100, max_cost_micros=100)


def definition(name, nodes, edges=(), **changes):
    plan = ExecutionPlan(
        "tenant-a",
        name,
        name,
        "policy-v1",
        tuple(nodes),
        tuple(edges),
        capabilities=("bpmn_control_v1",),
        budget=BUDGET,
    )
    return WorkflowComponent(name, "1.0.0", "policy-v1", replace(plan, **changes))


def register(registry, component):
    registry.register(component)
    return DefinitionPin(component.component_id, component.version, component_sha256(component))


def activation(identity, pin, *, kind="subprocess", **options):
    return ExecutionNode(
        identity,
        node_type="bpmn_activation",
        required_capabilities=("bpmn_control_v1",),
        metadata={ACTIVATION_KEY: {"kind": kind, "body": pin.to_dict(), **options}},
    )


def loop_options(maximum=3):
    return {
        "max_iterations": maximum,
        "entry_condition": {"op": "eq", "field": "input.enter", "value": True},
        "repeat_condition": {"op": "eq", "field": "results.work.again", "value": True},
        "body_flow_id": "body_flow",
        "back_flow_id": "back_flow",
        "exit_flow_id": "exit_flow",
    }


def loop_definition(maximum=3, *, body=None, options=None):
    registry = WorkflowComponentRegistry()
    body = body or definition("body", [ExecutionNode("work")])
    body_pin = register(registry, body)
    root = definition("root", [activation("loop", body_pin, kind="while", **(options or loop_options(maximum)))])
    root_pin = register(registry, root)
    return registry, root_pin, body


def compile_definition(registry, pin, **kwargs):
    return BpmnActivationCompiler(registry, **kwargs).compile(pin, tenant_id="tenant-a", policy_version="policy-v1")


def test_expansion_is_deterministic_serializable_and_bound_before_admission():
    registry, pin, body = loop_definition()
    before = deepcopy(body.to_dict())
    first = compile_definition(registry, pin)
    second = compile_definition(registry, pin)
    assert first.candidate_plan.to_dict() == second.candidate_plan.to_dict()
    assert first.candidate_plan.plan_hash != registry.resolve(pin.component_id, pin.version).plan.plan_hash
    assert ExecutionPlan.from_mapping(first.candidate_plan.to_dict()).plan_hash == first.candidate_plan.plan_hash
    assert body.to_dict() == before
    nodes = first.candidate_plan.nodes
    assert all(node.node_type in {"task", "bpmn_control"} for node in nodes)
    work = [node for node in nodes if node.node_type == "task"]
    assert len(work) == len({node.node_id for node in work}) == 3
    assert [node.metadata[ORIGIN_KEY]["path"][-1]["iteration"] for node in work] == [0, 1, 2]
    assert all(node.metadata[ORIGIN_KEY]["source_id"] == "work" for node in work)
    manifest = first.candidate_plan.metadata["bpmn_activation_expansion"]
    assert {item["source_id"] for item in manifest["edge_origins"].values()} == {"body_flow", "back_flow", "exit_flow"}
    assert len(manifest["edge_origins"]) == len(first.candidate_plan.edges)
    with pytest.raises(BpmnActivationError, match="request_projection_missing"):
        first.require_executable_plan()


def test_noop_shared_component_pass_keeps_expanded_admission_hash():
    from agent.services.workflow_runtime.components import WorkflowComponentCompiler

    registry, pin, _ = loop_definition()
    expanded = compile_definition(registry, pin).candidate_plan
    # Production installs this compiler even on plans without component nodes.
    assert WorkflowComponentCompiler(WorkflowComponentRegistry()).compile(expanded).plan_hash == expanded.plan_hash


@pytest.mark.parametrize(
    "field,value",
    [("max_iterations", True), ("max_iterations", 257), ("max_depth", 0), ("max_nodes", -1), ("max_edges", 0)],
)
def test_limits_are_strict_hub_configuration(field, value):
    with pytest.raises(BpmnActivationError, match="limit_invalid"):
        ActivationLimits(**{field: value})


@pytest.mark.parametrize("maximum", [-1, True, 1.5, "3", 33])
def test_loop_bound_must_be_an_integer_within_hub_limit(maximum):
    registry, pin, _ = loop_definition(maximum)
    with pytest.raises(BpmnActivationError, match="iteration_limit") as failure:
        compile_definition(registry, pin)
    assert failure.value.element_id == "loop"


@pytest.mark.parametrize(
    "limit,reason", [(ActivationLimits(max_nodes=5), "node_budget"), (ActivationLimits(max_edges=5), "edge_budget")]
)
def test_expanded_graph_is_bounded_not_just_source_graph(limit, reason):
    registry, pin, _ = loop_definition()
    with pytest.raises(BpmnActivationError, match=reason):
        compile_definition(registry, pin, limits=limit)


def test_pins_reject_missing_changed_and_compatible_fallback_versions():
    registry, pin, body = loop_definition()
    body.plan.nodes[0].metadata["changed"] = True
    with pytest.raises(BpmnActivationError, match="hash_drift") as failure:
        compile_definition(registry, pin)
    assert failure.value.element_id == "loop"
    empty = WorkflowComponentRegistry()
    with pytest.raises(BpmnActivationError, match="definition_missing"):
        compile_definition(empty, pin)
    compatible = replace(body, version="2.0.0", compatible_versions=("1.0.0",))
    register(empty, compatible)
    old_pin = DefinitionPin("body", "1.0.0", component_sha256(compatible))
    with pytest.raises(BpmnActivationError, match="version_drift"):
        compile_definition(empty, old_pin)


@pytest.mark.parametrize("version", ["latest", "^1.0", "1", "01.0.0", "1.0.0 "])
def test_no_latest_or_implicit_version_resolution(version):
    with pytest.raises(BpmnActivationError, match="exact_version_required"):
        DefinitionPin("body", version, "a" * 64)


def test_two_calls_of_same_pinned_body_get_distinct_source_bound_activations():
    registry = WorkflowComponentRegistry()
    body = register(registry, definition("body", [ExecutionNode("work")]))
    root = register(
        registry,
        definition(
            "root",
            [activation("first", body), activation("second", body)],
            [ExecutionEdge("first", "second", edge_id="next_call")],
        ),
    )
    expansion = compile_definition(registry, root)
    plan = expansion.candidate_plan
    assert len({node.node_id for node in plan.nodes}) == 2
    assert plan.edges[0].source == plan.nodes[0].node_id
    assert plan.edges[0].target == plan.nodes[1].node_id
    assert {issue.reason_code for issue in expansion.blockers} == {
        "bpmn_activation_request_projection_missing",
        "bpmn_activation_deadline_binding_required",
        "bpmn_activation_scoped_input_binding_required",
    }


def test_nested_calls_enforce_depth_before_delegation():
    registry = WorkflowComponentRegistry()
    current = register(registry, definition("leaf", [ExecutionNode("work")]))
    for name in ("middle", "root"):
        current = register(registry, definition(name, [activation("call", current)]))
    assert len(compile_definition(registry, current).candidate_plan.nodes) == 1
    with pytest.raises(BpmnActivationError, match="depth_exceeded"):
        compile_definition(registry, current, limits=ActivationLimits(max_depth=1))


@pytest.mark.parametrize(
    "field,value",
    [("input_mapping", {}), ("output_mapping", {}), ("local_variables", []), ("triggered_by_event", True)],
)
def test_subprocess_scope_and_event_features_fail_closed(field, value):
    registry = WorkflowComponentRegistry()
    body = register(registry, definition("body", [ExecutionNode("work")]))
    root = register(registry, definition("root", [activation("call", body, **{field: value})]))
    with pytest.raises(BpmnActivationError, match="scope_unsupported|semantics_unsupported"):
        compile_definition(registry, root)


@pytest.mark.parametrize(
    "budget",
    [
        replace(BUDGET, max_cost_micros=None),
        replace(BUDGET, timeout_seconds=float("inf")),
        replace(BUDGET, max_attempts=True),
    ],
)
def test_unbounded_or_coerced_budgets_are_rejected(budget):
    registry = WorkflowComponentRegistry()
    component = definition("root", [ExecutionNode("work")], budget=budget)
    if budget.timeout_seconds != float("inf"):
        pin = register(registry, component)
        with pytest.raises(BpmnActivationError, match="finite_budget_required"):
            compile_definition(registry, pin)
    else:
        # Canonical hashing itself refuses non-finite JSON before registration.
        with pytest.raises(ValueError, match="compliant"):
            component_sha256(component)


def test_child_cannot_widen_parent_budget_tools_tenant_or_policy():
    for changes, expected in [
        ({"budget": replace(BUDGET, max_cost_micros=101)}, "budget_escalation"),
        ({"tenant_id": "tenant-b"}, "authority_mismatch"),
    ]:
        registry = WorkflowComponentRegistry()
        body = register(registry, definition("body", [ExecutionNode("work")], **changes))
        root = register(registry, definition("root", [activation("call", body)]))
        with pytest.raises(BpmnActivationError, match=expected):
            compile_definition(registry, root)
    registry = WorkflowComponentRegistry()
    component = definition("body", [ExecutionNode("work", allowed_tools=("dangerous",))])
    body = register(registry, replace(component, allowed_tools=("dangerous",)))
    root = register(registry, definition("root", [activation("call", body)]))
    with pytest.raises(BpmnActivationError, match="authority_escalation"):
        compile_definition(registry, root)


def test_raw_cycle_is_rejected_with_source_element_reference():
    body = definition(
        "body",
        [ExecutionNode("a"), ExecutionNode("b")],
        [ExecutionEdge("a", "b", edge_id="forward"), ExecutionEdge("b", "a", edge_id="back")],
    )
    registry, pin, _ = loop_definition(body=body)
    with pytest.raises(BpmnActivationError, match="invalid_dag.*loop.*cycle"):
        compile_definition(registry, pin)


def test_zero_bound_does_not_hide_unsupported_body():
    registry, pin, _ = loop_definition(0, body=definition("body", [ExecutionNode("work", node_type="component")]))
    with pytest.raises(BpmnActivationError, match="node_unsupported"):
        compile_definition(registry, pin)


def test_mutable_registry_is_snapshotted_and_never_needed_after_compile():
    registry, pin, _ = loop_definition()
    expansion = compile_definition(registry, pin)
    before = expansion.candidate_plan.to_dict()
    registry.resolve("body", "1.0.0").plan.nodes[0].metadata["mutated"] = True
    assert expansion.candidate_plan.to_dict() == before
    with pytest.raises(BpmnActivationError, match="hash_drift"):
        compile_definition(registry, pin)


def test_recursive_reference_is_rejected_before_resolving_child():
    registry = WorkflowComponentRegistry()
    recursive = DefinitionPin("root", "1.0.0", "0" * 64)
    root = register(registry, definition("root", [activation("call", recursive)]))
    with pytest.raises(BpmnActivationError, match="recursion_unsupported"):
        compile_definition(registry, root)


@pytest.mark.parametrize(
    "condition,reason",
    [
        ({"op": "eq", "field": "results.foreign.again", "value": True}, "result_scope_unsupported"),
        ({"op": "eq", "field": "input..again", "value": True}, "condition_invalid"),
        ({"op": "eq", "field": "input.again", "value": {}}, "literal_invalid"),
        ({"op": "always", "ignored": "hidden condition"}, "condition_invalid"),
        ({"op": "any", "conditions": [{"op": "always"}] * 257}, "condition_invalid"),
    ],
)
def test_unsupported_or_unbound_loop_condition_is_rejected(condition, reason):
    options = loop_options()
    options["repeat_condition"] = condition
    registry, pin, _ = loop_definition(options=options)
    with pytest.raises(BpmnActivationError, match=reason):
        compile_definition(registry, pin)


def test_deep_condition_has_a_bounded_compile_diagnostic():
    condition = {"op": "always"}
    for _ in range(18):
        condition = {"op": "not", "condition": condition}
    options = loop_options()
    options["repeat_condition"] = condition
    registry, pin, _ = loop_definition(options=options)
    with pytest.raises(BpmnActivationError, match="condition_limit"):
        compile_definition(registry, pin)


def test_loop_gates_are_distinct_for_each_activation():
    from agent.services.workflow_runtime.execution_plan import ExecutionGate

    body = definition("body", [ExecutionNode("work", gate_id="approve")], gates=(ExecutionGate("approve"),))
    registry, pin, _ = loop_definition(body=body)
    plan = compile_definition(registry, pin).candidate_plan
    gate_ids = {gate.gate_id for gate in plan.gates}
    assert len(gate_ids) == 3
    assert {node.gate_id for node in plan.nodes if node.node_type == "task"} == gate_ids


def test_changed_root_pin_changes_activation_identity_and_admission_hash():
    registry, pin, _ = loop_definition()
    first = compile_definition(registry, pin).candidate_plan
    newer = replace(registry.resolve("root", "1.0.0"), version="1.0.1")
    new_pin = register(registry, newer)
    second = compile_definition(registry, new_pin).candidate_plan
    assert first.plan_hash != second.plan_hash
    assert not {node.node_id for node in first.nodes}.intersection(node.node_id for node in second.nodes)
