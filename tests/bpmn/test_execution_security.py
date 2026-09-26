"""Synthetic fail-closed regression cases; no production admission evidence."""

from dataclasses import replace

import pytest

from agent.services.workflow_backend import WorkflowRequest
from agent.visual_process.blueprint_mapper import graph_to_blueprint_dict
from agent.visual_process.bpmn_adapter import export_bpmn_xml, import_bpmn_xml
from tests.bpmn.test_execution_admission import diagram
from tests.bpmn.test_execution_routing import compile_request, xor_xml


@pytest.mark.parametrize(
    "body",
    [
        '<task id="t"><serviceTask id="hidden"/></task>',
        '<task id="t"/><sequenceFlow id="f" sourceRef="t" targetRef="missing"/>',
        "<task/>",
        '<task id="t"><extensionElements><metadata xmlns="https://ananta.local/bpmn">[]</metadata></extensionElements></task>',
        '<startEvent id="s"><extensionElements><metadata xmlns="https://ananta.local/bpmn">{"kind":"tool_task"}</metadata></extensionElements></startEvent>',
        '<userTask id="t"><extensionElements><metadata xmlns="https://ananta.local/bpmn">{"kind":"tool_task","gate":false}</metadata></extensionElements></userTask>',
    ],
)
def test_imported_metadata_and_nested_elements_cannot_replace_semantics(body):
    with pytest.raises(ValueError, match="bpmn"):
        compile_request(diagram(body))


def test_foreign_condition_language_is_not_silently_interpreted():
    with pytest.raises(ValueError, match="language_unsupported"):
        compile_request(xor_xml().replace("<conditionExpression>", '<conditionExpression language="feel">'))
    with pytest.raises(ValueError, match="definition_attribute_unsupported"):
        compile_request(
            xor_xml().replace(
                'targetNamespace="urn:test:bpmn"', 'targetNamespace="urn:test:bpmn" expressionLanguage="xpath"'
            )
        )


def test_empty_condition_is_not_an_unconditional_flow():
    with pytest.raises(ValueError, match="expression_required"):
        compile_request(xor_xml().replace("input.approved == True", ""))


def test_invalid_expression_syntax_has_a_non_executable_preview():
    graph = import_bpmn_xml(xor_xml().replace("input.approved == True", "input.approved === True")).graph
    assert graph.metadata["bpmn_execution_report"]["supported"] is False
    assert any(issue["element_id"] == "yes" for issue in graph.metadata["bpmn_execution_report"]["issues"])
    from agent.visual_process.blueprint_mapper import graph_to_workflow_request

    with pytest.raises(ValueError, match="expression_unsupported"):
        graph_to_workflow_request(graph, policy_scope={"source": "test"})


def test_gateway_cannot_depend_on_scheduling_of_non_predecessor_result():
    with pytest.raises(ValueError, match="unbound_result_reference"):
        compile_request(xor_xml().replace("input.approved == True", "results.no_task.value == True"))


def test_direct_control_plan_requires_bpmn_capability():
    from tests.bpmn.test_execution_routing import execution_plan

    plan = execution_plan(compile_request(xor_xml()))
    assert any(issue.code == "bpmn_capability_required" for issue in replace(plan, capabilities=()).validate())


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf"), True, 0.0])
def test_run_budget_requires_a_finite_positive_timeout(timeout):
    from agent.services.workflow_runtime.execution_plan import ExecutionBudget

    assert any(issue.code == "budget_timeout_invalid" for issue in ExecutionBudget(timeout_seconds=timeout).validate())


def test_overflowing_deadline_cannot_create_events_or_delegate_work():
    from agent.services.native_graph_orchestration_service import NativeGraphRequest
    from tests.bpmn.test_execution_recovery import restart
    from tests.bpmn.test_execution_routing import execution_plan
    from tests.test_native_graph_runtime import runtime

    _, queue, _, keys, ledger, stores = runtime()
    hub = restart(queue, keys, ledger, stores, clock=lambda: 1e308)
    plan = execution_plan(compile_request(xor_xml()))
    plan = replace(plan, budget=replace(plan.budget, timeout_seconds=1e308))
    with pytest.raises(ValueError, match="bpmn_deadline_invalid"):
        hub.start(NativeGraphRequest(plan, "synthetic-overflow", "control", input_data={"approved": True}))
    assert queue.submissions == []
    assert stores["events"].list_events(tenant_id="tenant-a", run_id="synthetic-overflow") == ()


def test_catalog_distinguishes_contract_support_from_release_verification():
    from agent.visual_process.bpmn_execution_support import capability_catalog

    catalog = capability_catalog()
    assert catalog["version"] == "1.0"
    assert catalog["runtime_verified"] is False
    assert catalog["runtimes"]["temporal"] == "unsupported"
    supported = {item["element"] for item in catalog["elements"] if item["execution_contract_supported"]}
    assert not supported.intersection(catalog["unsupported"])
    assert {"exclusiveGateway", "parallelGateway", "userTask"} <= supported


def test_definition_hash_and_removed_source_are_revalidated():
    request = compile_request(xor_xml())
    raw = request.to_dict()
    raw["execution_graph"]["definition_hash"] = "not-the-bound-source"
    assert "bpmn_execution_graph_mismatch" in WorkflowRequest.from_mapping(raw).validate()
    metadata = dict(request.metadata)
    metadata.pop("bpmn_source_xml")
    assert "bpmn_source_required" in replace(request, metadata=metadata).validate()


def test_default_flow_survives_xml_export_import():
    graph = import_bpmn_xml(xor_xml(default=True)).graph
    restored = compile_request(export_bpmn_xml(graph).bpmn_xml)
    assert restored.execution_graph["controls"]["choose"]["outgoing"][1]["default"] is True


def test_legacy_blueprint_path_cannot_erase_gateway_conditions():
    with pytest.raises(ValueError, match="legacy_blueprint_unsupported"):
        graph_to_blueprint_dict(import_bpmn_xml(xor_xml()).graph)


def test_unknown_semantics_cannot_disappear_in_normalized_backend_export():
    graph = import_bpmn_xml(
        xor_xml().replace('<startEvent id="start"/>', '<startEvent id="start"><timerEventDefinition/></startEvent>')
    ).graph
    with pytest.raises(ValueError, match="bpmn"):
        export_bpmn_xml(graph)


def test_loop_cannot_be_silently_flattened():
    xml = xor_xml().replace("</process>", '<sequenceFlow id="back" sourceRef="yes_task" targetRef="choose"/></process>')
    with pytest.raises(ValueError, match="loop_unsupported"):
        compile_request(xml)


def test_empty_or_wrong_xml_source_returns_structured_execution_error():
    from agent.visual_process.blueprint_mapper import graph_to_workflow_request
    from agent.visual_process.bpmn_execution_support import BpmnExecutionError

    graph = import_bpmn_xml(xor_xml()).graph
    graph.metadata["bpmn_source_xml"] = "<not-bpmn/>"
    with pytest.raises(BpmnExecutionError, match="source_invalid"):
        graph_to_workflow_request(graph, policy_scope={"source": "test"})


def test_conflicting_extension_condition_cannot_override_xml():
    xml = xor_xml().replace(
        "<conditionExpression>input.approved == True</conditionExpression>",
        "<conditionExpression>input.approved == True</conditionExpression>"
        '<extensionElements><metadata xmlns="https://ananta.local/bpmn">{"condition":{"kind":"always"}}</metadata></extensionElements>',
    )
    with pytest.raises(ValueError, match="condition_binding_mismatch"):
        compile_request(xml)


def test_plan_roundtrip_preserves_edge_ids_and_hash_binds_expression():
    from agent.services.workflow_runtime.execution_plan import ExecutionPlan
    from tests.bpmn.test_execution_routing import execution_plan

    first = execution_plan(compile_request(xor_xml()))
    assert ExecutionPlan.from_mapping(first.to_dict()).plan_hash == first.plan_hash
    assert {edge.edge_id for edge in first.edges} == {"begin", "yes", "no", "child", "yes_join", "no_join", "finish"}
    second = execution_plan(compile_request(xor_xml(second="True")))
    assert second.plan_hash != first.plan_hash


def test_production_component_pass_preserves_plain_bpmn_plan_hash():
    from agent.services.workflow_runtime.components import WorkflowComponentCompiler, WorkflowComponentRegistry
    from tests.bpmn.test_execution_routing import execution_plan

    plan = execution_plan(compile_request(xor_xml()))
    compiler = WorkflowComponentCompiler(WorkflowComponentRegistry())
    assert compiler.compile(plan).plan_hash == plan.plan_hash


def test_cycle_check_is_not_recursive_for_large_admitted_graphs():
    from agent.visual_process.models import VisualProcessEdge, VisualProcessGraph, VisualProcessStep

    graph = VisualProcessGraph(
        id="large",
        name="Synthetic chain",
        steps=[VisualProcessStep(id=f"n{i}", label=f"n{i}") for i in range(1500)],
        edges=[VisualProcessEdge(id=f"e{i}", source=f"n{i}", target=f"n{i + 1}") for i in range(1499)],
    )
    assert graph.has_cycles() is False
    graph.edges.append(VisualProcessEdge(id="loop", source="n1499", target="n0"))
    assert graph.has_cycles() is True


@pytest.mark.parametrize("expression", ["input.value == 1e999", "input.value == -1e999"])
def test_non_finite_expression_literals_are_rejected(expression):
    from agent.visual_process.bpmn_conditions import compile_condition

    with pytest.raises(ValueError, match="expression_unsupported"):
        compile_condition(expression, element_id="edge")


@pytest.mark.parametrize(
    "path,body_kind",
    [
        ("/workflow-request", "graph"),
        ("/workflow/start", "graph"),
        ("/workflow/start", "request"),
    ],
)
def test_http_admission_fails_before_runtime_selection_or_reservation(monkeypatch, path, body_kind):
    from flask import Flask

    from agent.routes import visual_process as routes

    # Authentication is independently covered by route-security tests. This
    # isolated transport fixture exercises the authenticated handler itself.
    app = Flask(__name__)
    app.testing = True
    handler = routes.workflow_request if path == "/workflow-request" else routes.workflow_start
    app.add_url_rule(path, view_func=handler.__wrapped__, methods=["POST"])

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid semantics reached runtime selection / ownership writes")

    monkeypatch.setattr(routes, "configured_workflow_backend", forbidden)
    monkeypatch.setattr(routes.workflow_route_authorization_service, "reserve", forbidden)
    if body_kind == "graph":
        graph = import_bpmn_xml(
            xor_xml().replace('<startEvent id="start"/>', '<startEvent id="start"><timerEventDefinition/></startEvent>')
        ).graph
        body = {"graph": graph.model_dump(), "policy_scope": {"source": "synthetic"}}
    else:
        request = compile_request(xor_xml()).to_dict()
        request.pop("execution_graph")
        body = {"workflow_request": request}
    response = app.test_client().post(path, json=body)
    assert response.status_code == 422, response.get_json()
    assert "bpmn" in str(response.get_json())
