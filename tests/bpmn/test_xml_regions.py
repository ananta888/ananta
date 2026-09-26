"""Synthetic bounded XML region checks; never production release evidence."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import replace
from html import escape

import pytest

from agent.services.bpmn_input_projection import project_bpmn_inputs
from agent.services.workflow_backend import WorkflowRequest
from agent.visual_process.blueprint_mapper import graph_to_workflow_request
from agent.visual_process.bpmn_adapter import export_bpmn_xml, import_bpmn_xml
from agent.visual_process.bpmn_xml_region_contracts import ORIGIN_KEY, projection


def meta(value):
    return "<extensionElements><ananta:metadata>" + escape(json.dumps(value)) + "</ananta:metadata></extensionElements>"


def document(body):
    return (
        '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" '
        'xmlns:ananta="https://ananta.local/bpmn" id="definitions" targetNamespace="urn:synthetic">'
        '<process id="process">' + body + "</process></definitions>"
    )


def flow(identity, source, target, expression=None):
    condition = "" if expression is None else "<conditionExpression>" + escape(expression) + "</conditionExpression>"
    return f'<sequenceFlow id="{identity}" sourceRef="{source}" targetRef="{target}">{condition}</sequenceFlow>'


def wrap(activity, identity="region"):
    return document(
        '<startEvent id="start"/>'
        + activity
        + '<endEvent id="end"/>'
        + flow("enter", "start", identity)
        + flow("leave", identity, "end")
    )


def subprocess_xml(*, child_projection=True, output_mapping=None):
    spec = {
        "schema": "ananta.bpmn_region.v1",
        "input_mapping": {"value": ["input", "public"]},
        "output_mapping": {"value": ["results", "work", "value"]} if output_mapping is None else output_mapping,
    }
    view = meta({"bpmn_input_projection": projection({"value": ["input", "value"]})}) if child_projection else ""
    return wrap(
        '<subProcess id="region">'
        + meta({"bpmn_region": spec})
        + '<startEvent id="inner_start"/>'
        + '<serviceTask id="work">'
        + view
        + '</serviceTask><endEvent id="inner_end"/>'
        + flow("inner_enter", "inner_start", "work")
        + flow("inner_leave", "work", "inner_end")
        + "</subProcess>"
    )


def loop_xml(*, maximum=3, condition="input.value < 3", test_before="true", repeat=None):
    spec = {
        "schema": "ananta.bpmn_loop.v1",
        "input_mapping": {"value": ["input", "value"]},
        "repeat_mapping": {"value": ["results", "again", "value"]} if repeat is None else repeat,
    }
    return wrap(
        '<serviceTask id="again">'
        + meta({"bpmn_loop": spec, "bpmn_input_projection": projection({"value": ["input", "value"]})})
        + f'<standardLoopCharacteristics testBefore="{test_before}" loopMaximum="{maximum}">'
        + "<loopCondition>"
        + escape(condition)
        + "</loopCondition></standardLoopCharacteristics></serviceTask>",
        "again",
    )


def compile_xml(xml):
    graph = import_bpmn_xml(xml).graph
    request = graph_to_workflow_request(graph, policy_scope={"source": "synthetic-test"})
    # A Hub fixture supplies finite runtime authority; XML never mints budgets.
    return graph, replace(
        request,
        metadata={
            **request.metadata,
            "execution_budget": {
                "max_attempts": 1,
                "timeout_seconds": 300.0,
                "max_tokens": 100,
                "max_cost_micros": 100,
            },
        },
    )


def test_subprocess_is_deterministic_source_bound_and_request_ids_are_expanded():
    xml = subprocess_xml()
    graph, request = compile_xml(xml)
    second, repeated = compile_xml(xml)
    assert graph.definition_hash() == second.definition_hash()
    assert (
        replace(request, correlation_id="synthetic").to_dict()
        == replace(repeated, correlation_id="synthetic").to_dict()
    )
    assert request.validate() == []
    assert not graph.has_cycles()
    assert set(graph.step_ids()) == {step.step_id for step in request.steps}
    assert "work" not in graph.step_ids()
    assert export_bpmn_xml(graph).bpmn_xml == xml
    assert graph.metadata["bpmn_source_xml"] == xml
    for step in graph.steps:
        assert step.metadata[ORIGIN_KEY]["source_sha256"] == graph.metadata["bpmn_source_sha256"]
    assert request.execution_graph["controls"]["region"]["kind"] == "projection"
    assert (
        request.execution_graph["node_metadata"]["region"]["bpmn_input_projection"]["workflow_input"]["value"][0]
        == "results"
    )


def test_subprocess_child_receives_only_explicit_public_field():
    graph, _ = compile_xml(subprocess_xml())
    entry = next(step for step in graph.steps if step.metadata[ORIGIN_KEY]["role"] == "entry")
    child = next(step for step in graph.steps if step.metadata[ORIGIN_KEY]["source_id"] == "work")
    initial = {"public": 7, "secret": "never-delegate"}
    admitted = project_bpmn_inputs(entry.metadata["bpmn_input_projection"], input_data=initial, results={})
    scoped = project_bpmn_inputs(
        child.metadata["bpmn_input_projection"],
        input_data=initial,
        results={entry.id: admitted["workflow_input"], "unrelated": {"secret": "no"}},
    )
    assert scoped == {"workflow_input": {"value": 7}, "dependency_results": {}}
    with pytest.raises(ValueError, match="field_missing"):
        project_bpmn_inputs(child.metadata["bpmn_input_projection"], input_data={"value": 9}, results={})


@pytest.mark.parametrize("key", ["bpmn_input_projection", ORIGIN_KEY, "bpmn_region_control"])
def test_region_metadata_is_bound_in_graph_and_request(key):
    graph, request = compile_xml(subprocess_xml())
    graph.step_by_id("region").metadata.pop(key)
    with pytest.raises(ValueError, match="binding_mismatch"):
        graph_to_workflow_request(graph)
    raw = request.to_dict()
    next(step for step in raw["steps"] if step["step_id"] == "region")["metadata"].pop(key)
    assert "bpmn_step_metadata_binding_mismatch" in WorkflowRequest.from_mapping(raw).validate()


def test_loop_has_unique_per_iteration_ids_explicit_state_and_bounded_dag():
    graph, request = compile_xml(loop_xml())
    assert request.validate() == []
    bodies = [step for step in graph.steps if step.metadata[ORIGIN_KEY]["role"] == "body"]
    assert len(bodies) == 3
    assert {step.metadata[ORIGIN_KEY]["scope"][-1]["iteration"] for step in bodies} == {0, 1, 2}
    assert len({step.id for step in graph.steps}) == len(graph.steps)
    assert not graph.has_cycles()
    join = graph.step_by_id("again").metadata["bpmn_projection_join"]
    assert len(join["sources"]) == 4
    assert set(join["sources"]) == {edge.source for edge in graph.edges_to("again")}
    assert (
        compile_xml(loop_xml(maximum=2))[1].execution_graph["definition_hash"]
        != request.execution_graph["definition_hash"]
    )


@pytest.mark.parametrize(
    "xml,reason",
    [
        (subprocess_xml(child_projection=False), "bpmn_region_child_projection_required"),
        (subprocess_xml(output_mapping={"value": ["results", "outside", "value"]}), "bpmn_region_result_unknown"),
        (loop_xml(maximum=0), "bpmn_loop_finite_maximum_required"),
        (loop_xml(maximum=33), "bpmn_loop_finite_maximum_required"),
        (loop_xml(maximum="infinite"), "bpmn_loop_finite_maximum_required"),
        (loop_xml(test_before="false"), "bpmn_loop_test_before_required"),
        (loop_xml(condition="results.again.value < 2"), "bpmn_loop_condition_scope_invalid"),
        (loop_xml(repeat={"other": ["input", "value"]}), "bpmn_loop_state_fields_mismatch"),
        (wrap('<callActivity id="region" calledElement="unpinned"/>'), "bpmn_child_unsupported"),
    ],
)
def test_unsupported_regions_remain_editable_but_not_executable(xml, reason):
    graph = import_bpmn_xml(xml).graph
    assert graph.metadata["bpmn_execution_report"]["supported"] is False
    with pytest.raises(ValueError, match=reason):
        graph_to_workflow_request(graph)


def test_subprocess_requires_a_single_connected_start_and_end():
    xml = subprocess_xml().replace(
        '<endEvent id="inner_end"/>', '<endEvent id="inner_end"/><startEvent id="unconnected"/>'
    )
    with pytest.raises(ValueError, match="single_entry_exit"):
        compile_xml(xml)


def test_arbitrary_sequence_flow_backedge_is_not_promoted_to_a_structured_loop():
    xml = subprocess_xml().replace("</subProcess>", flow("back", "work", "inner_start") + "</subProcess>")
    with pytest.raises(ValueError, match="bpmn_loop_unsupported"):
        compile_xml(xml)


def test_scoped_gateway_cannot_read_unprojected_input():
    xml = (
        subprocess_xml()
        .replace('<serviceTask id="work">', '<exclusiveGateway id="work">')
        .replace("</serviceTask>", "</exclusiveGateway>")
    )
    xml = xml.replace(
        flow("inner_leave", "work", "inner_end"), flow("inner_leave", "work", "inner_end", "input.secret == True")
    )
    with pytest.raises(ValueError, match="bpmn_condition_projection_unbound"):
        compile_xml(xml)


def test_future_sibling_result_cannot_be_used_in_a_child_projection():
    xml = subprocess_xml().replace(
        "[&quot;input&quot;, &quot;value&quot;]", "[&quot;results&quot;, &quot;inner_end&quot;, &quot;value&quot;]"
    )
    with pytest.raises(ValueError, match="bpmn_projection_result_not_predecessor"):
        compile_xml(xml)


@pytest.mark.parametrize("field", ["bpmn_region_control", "bpmn_activation_origin", "bpmn_projection_join"])
def test_xml_cannot_supply_compiler_owned_fields(field):
    xml = wrap('<task id="region">' + meta({field: "forged"}) + "</task>")
    with pytest.raises(ValueError, match="bpmn_region_metadata_reserved"):
        compile_xml(xml)


def test_expansion_limits_are_enforced_during_emission():
    from agent.visual_process.bpmn_activation_contracts import ActivationLimits
    from agent.visual_process.bpmn_execution_support import parse_bpmn
    from agent.visual_process.bpmn_xml_regions import expand_xml_regions

    with pytest.raises(ValueError, match="node_budget_exceeded"):
        expand_xml_regions(parse_bpmn(loop_xml()), source_sha256="0" * 64, limits=ActivationLimits(max_nodes=5))


def nested_xml(depth):
    child_id = "work"
    body = (
        '<serviceTask id="work">'
        + meta({"bpmn_input_projection": projection({"value": ["input", "value"]})})
        + "</serviceTask>"
    )
    for index in reversed(range(depth)):
        identity, start, end = f"region{index}", f"start{index}", f"end{index}"
        spec = {
            "schema": "ananta.bpmn_region.v1",
            "input_mapping": {"value": ["input", "value"]},
            "output_mapping": {"value": ["results", child_id, "value"]},
        }
        body = (
            f'<subProcess id="{identity}">'
            + meta({"bpmn_region": spec})
            + f'<startEvent id="{start}"/>'
            + body
            + f'<endEvent id="{end}"/>'
            + flow(f"in{index}", start, child_id)
            + flow(f"out{index}", child_id, end)
            + "</subProcess>"
        )
        child_id = identity
    return wrap(body, child_id)


def test_nested_subprocesses_preserve_local_bindings_and_reject_depth_overflow():
    graph, request = compile_xml(nested_xml(8))
    assert request.validate() == []
    assert max(len(step.metadata[ORIGIN_KEY]["scope"]) for step in graph.steps) == 8
    with pytest.raises(ValueError, match="bpmn_activation_depth_exceeded"):
        compile_xml(nested_xml(9))


def test_outer_tasks_cannot_use_ambient_child_results():
    xml = subprocess_xml().replace('<endEvent id="end"/>', '<task id="after"/><endEvent id="end"/>')
    xml = xml.replace(flow("leave", "region", "end"), flow("leave", "region", "after") + flow("finish", "after", "end"))
    with pytest.raises(ValueError, match="bpmn_region_child_projection_required"):
        compile_xml(xml)


def test_scoped_projection_unknown_alias_and_invalid_dictionary_path_fail_before_adaptation():
    xml = subprocess_xml().replace("[&quot;input&quot;, &quot;value&quot;]", "[&quot;input&quot;, &quot;other&quot;]")
    with pytest.raises(ValueError, match="bpmn_region_input_unknown"):
        compile_xml(xml)
    xml = subprocess_xml().replace("[&quot;input&quot;, &quot;value&quot;]", "[&quot;input&quot;, 0]")
    with pytest.raises(ValueError, match="bpmn_region_projection_invalid"):
        compile_xml(xml)


def test_children_cannot_expand_parent_tools_or_policy_scope():
    xml = subprocess_xml()
    xml = xml.replace(
        "{&quot;bpmn_input_projection&quot;",
        "{&quot;allowed_tools&quot;: [&quot;read&quot;], &quot;bpmn_input_projection&quot;",
    )
    graph = import_bpmn_xml(xml).graph
    with pytest.raises(ValueError, match="bpmn_region_tool_escalation"):
        graph_to_workflow_request(graph, allowed_tools=[])
    request = graph_to_workflow_request(graph, allowed_tools=["read"], policy_scope={"source": "synthetic-test"})
    assert request.validate() == []
    raw = request.to_dict()
    child = next(step for step in raw["steps"] if step["metadata"][ORIGIN_KEY]["source_id"] == "work")
    child["allowed_tools"] = ["write"]
    child["policy_scope"] = {"source": "other"}
    errors = WorkflowRequest.from_mapping(raw).validate()
    assert "bpmn_region_tool_escalation" in errors
    assert "bpmn_region_policy_binding_mismatch" in errors


def xml_runtime(monkeypatch, *, xml=None, input_data=None):
    from agent.services.native_graph_orchestration_service import NativeGraphRequest
    from tests.bpmn.test_execution_routing import execution_plan
    from tests.test_native_graph_runtime import runtime

    _, compiled = compile_xml(xml or loop_xml())
    hub, queue, handler, keys, ledger, stores = runtime()
    original = handler.execute

    def increment(command, *, hub_task_id):
        answer = original(command, hub_task_id=hub_task_id)
        return replace(
            answer,
            output_data={"value": command.input_data["workflow_input"]["value"] + 1, "private_child": "local-only"},
        )

    monkeypatch.setattr(handler, "execute", increment)
    assignment = NativeGraphRequest(
        execution_plan(compiled),
        "synthetic-xml",
        "control",
        input_data={"value": 0} if input_data is None else input_data,
    )
    return hub, queue, handler, keys, ledger, stores, assignment


def advance_until(hub, assignment, result, predicate):
    for _ in range(100):
        if predicate(result):
            return result
        result = hub.advance(assignment)
    pytest.fail("bounded synthetic XML run failed to reach the expected state")


def test_subprocess_exports_only_explicit_fields(monkeypatch):
    hub, queue, handler, *_, assignment = xml_runtime(monkeypatch, xml=subprocess_xml(), input_data={"public": 4})
    result = advance_until(hub, assignment, hub.start(assignment), lambda value: value.status != "running")
    assert result.status == "completed", result.reason_code
    assert result.checkpoint.state.business_data["node_results"]["region"] == {"value": 5}


@pytest.mark.parametrize("xml", [subprocess_xml(), loop_xml()])
def test_missing_entry_field_fails_without_delegation(monkeypatch, xml):
    hub, queue, handler, *_, assignment = xml_runtime(monkeypatch, xml=xml, input_data={})
    result = advance_until(hub, assignment, hub.start(assignment), lambda value: value.status != "running")
    assert result.status == "failed"
    assert result.reason_code == "bpmn_input_projection_field_missing"
    assert not queue.submissions


def test_xml_loop_restart_preserves_iteration_assignments_and_final_state(monkeypatch):
    from tests.bpmn.test_execution_recovery import restart

    hub, queue, handler, keys, ledger, stores, assignment = xml_runtime(monkeypatch)
    result = advance_until(hub, assignment, hub.start(assignment), lambda _: len(queue.submissions) == 1)
    hub = restart(queue, keys, ledger, stores)
    result = advance_until(hub, assignment, result, lambda value: value.status != "running")
    assert result.status == "completed", result.reason_code
    assert len(queue.submissions) == len(set(handler.calls)) == 3
    assert result.checkpoint.state.business_data["node_results"]["again"] == {"value": 3}


def test_xml_loop_stale_result_cannot_finish_a_later_iteration(monkeypatch):
    hub, queue, handler, *_, assignment = xml_runtime(monkeypatch)
    result = advance_until(hub, assignment, hub.start(assignment), lambda _: len(queue.submissions) == 1)
    stale = next(iter(queue.results.values()))
    advance_until(hub, assignment, result, lambda _: len(queue.submissions) == 2)
    task_id, current = next(iter(queue.results.items()))
    queue.results[task_id] = replace(stale, node_id=current.node_id)
    with pytest.raises(ValueError, match="result_binding_mismatch"):
        hub.advance(assignment)
    assert len(queue.submissions) == 2


@pytest.mark.parametrize("xml,input_data", [(loop_xml(), {"value": 0}), (subprocess_xml(), {"public": 0})])
def test_xml_region_cancellation_stops_children(monkeypatch, xml, input_data):
    from tests.test_native_graph_runtime import signed_control

    hub, queue, handler, keys, *_, assignment = xml_runtime(monkeypatch, xml=xml, input_data=input_data)
    result = advance_until(hub, assignment, hub.start(assignment), lambda _: len(queue.submissions) == 1)
    result = hub.resume(
        assignment,
        command=signed_control(
            keys=keys,
            checkpoint=result.checkpoint,
            command_type="cancel",
            step_id="__workflow__",
            command_id="cancel-xml",
        ),
    )
    assert result.status == "cancelled"
    assert len(queue.cancelled) == 1
    assert hub.advance(assignment).status == "cancelled"
    assert len(queue.submissions) == 1


def looped_subprocess_xml():
    from agent.visual_process.bpmn_execution_support import parse_bpmn
    from agent.visual_process.bpmn_xml_region_contracts import metadata, set_metadata, tag

    root = parse_bpmn(subprocess_xml())
    activity = root.find(f".//{tag('subProcess')}")
    values = metadata(activity)
    values["bpmn_region"]["input_mapping"] = {"value": ["input", "value"]}
    values["bpmn_loop"] = {
        "schema": "ananta.bpmn_loop.v1",
        "input_mapping": {"value": ["input", "value"]},
        "repeat_mapping": {"value": ["results", "region", "value"]},
    }
    set_metadata(activity, values)
    loop = ET.SubElement(activity, tag("standardLoopCharacteristics"), testBefore="true", loopMaximum="3")
    ET.SubElement(loop, tag("loopCondition")).text = "input.value < 3"
    return ET.tostring(root, encoding="unicode")


def test_looped_embedded_subprocess_uses_exported_state_for_next_iteration(monkeypatch):
    xml = looped_subprocess_xml()
    graph, request = compile_xml(xml)
    assert request.validate() == []
    assert len([step for step in graph.steps if step.metadata[ORIGIN_KEY]["source_id"] == "work"]) == 3
    hub, queue, handler, *_, assignment = xml_runtime(monkeypatch, xml=xml)
    result = advance_until(hub, assignment, hub.start(assignment), lambda value: value.status != "running")
    assert result.status == "completed", result.reason_code
    assert len(queue.submissions) == 3
    assert result.checkpoint.state.business_data["node_results"]["region"] == {"value": 3}


def test_child_failure_does_not_publish_a_subprocess_exit(monkeypatch):
    hub, queue, handler, *_, assignment = xml_runtime(monkeypatch, xml=subprocess_xml(), input_data={"public": 0})
    original = handler.execute

    def fail(command, *, hub_task_id):
        answer = original(command, hub_task_id=hub_task_id)
        return replace(answer, status="failed", reason_code="synthetic-child-failure")

    monkeypatch.setattr(handler, "execute", fail)
    result = advance_until(hub, assignment, hub.start(assignment), lambda value: value.status != "running")
    assert result.status == "failed"
    assert "region" not in result.completed_node_ids


def test_request_cannot_replace_source_manifest_or_append_unbound_region_step():
    _, request = compile_xml(subprocess_xml())
    raw = request.to_dict()
    raw["metadata"]["bpmn_source_sha256"] = "f" * 64
    assert "bpmn_region_source_binding_mismatch" in WorkflowRequest.from_mapping(raw).validate()
    assert "bpmn_step_binding_mismatch" in replace(request, steps=(*request.steps, request.steps[0])).validate()


def projection_join_node(sources=("a", "b")):
    from agent.services.workflow_runtime.execution_plan import ExecutionNode

    return ExecutionNode(
        "join",
        node_type="bpmn_control",
        metadata={
            "bpmn_control": {"kind": "projection", "outgoing": []},
            "bpmn_input_projection": projection(),
            "bpmn_projection_join": {"schema": "ananta.bpmn_projection_join.v1", "sources": list(sources)},
        },
    )


def test_projection_join_selects_only_one_export_and_detaches_the_result():
    from agent.services.bpmn_projection_control import project_control_result, validate_projection_control

    node = projection_join_node()
    assert validate_projection_control(node, incoming_sources=["b", "a"]) == ()
    sources = {"b": {"value": {"nested": 7}}, "unrelated": {"private": 9}}
    output = project_control_result(
        node,
        input_data={"ambient": "no"},
        results=sources,
        completed_node_ids={"b", "unrelated"},
        skipped_node_ids={"a"},
    )
    assert output == {"value": {"nested": 7}}
    output["value"]["nested"] = 8
    assert sources["b"]["value"]["nested"] == 7


@pytest.mark.parametrize(
    "completed,skipped,results,reason",
    [
        (set(), {"a", "b"}, {}, "single_source_required"),
        ({"a", "b"}, set(), {"a": {}, "b": {}}, "single_source_required"),
        ({"a"}, set(), {"a": {}}, "sources_not_terminal"),
        ({"a"}, {"a", "b"}, {"a": {}}, "state_conflict"),
        ({"a"}, {"b"}, {}, "object_result_required"),
        ({"a"}, {"b"}, {"a": 1}, "object_result_required"),
    ],
)
def test_projection_join_invalid_activation_never_falls_back(completed, skipped, results, reason):
    from agent.services.bpmn_projection_control import project_control_result

    with pytest.raises(ValueError, match=reason):
        project_control_result(
            projection_join_node(),
            input_data={"value": "ambient"},
            results=results,
            completed_node_ids=completed,
            skipped_node_ids=skipped,
        )


def test_projection_join_contract_requires_closed_predecessors_and_empty_input_view():
    from agent.services.bpmn_projection_control import validate_projection_control

    assert "bpmn_projection_join_predecessors_mismatch" in validate_projection_control(
        projection_join_node(), incoming_sources=["a", "b", "outside"]
    )
    assert "bpmn_projection_join_sources_invalid" in validate_projection_control(projection_join_node(("a", "a")))
    node = projection_join_node()
    node.metadata["bpmn_input_projection"] = projection({"value": ["input", "ambient"]})
    assert "bpmn_projection_join_empty_view_required" in validate_projection_control(node)


def test_native_region_plan_requires_activation_capability():
    from tests.bpmn.test_execution_routing import execution_plan

    _, request = compile_xml(loop_xml())
    plan = execution_plan(request)
    narrowed = replace(plan, capabilities=tuple(value for value in plan.capabilities if value != "bpmn_activation_v1"))
    assert "bpmn_activation_capability_required" in {issue.code for issue in narrowed.validate()}


def test_region_budget_requires_explicit_finite_cost_and_cannot_expand_child_limits():
    from agent.services.bpmn_projection_control import validate_region_budget
    from tests.bpmn.test_execution_routing import execution_plan

    _, request = compile_xml(loop_xml())
    plan = execution_plan(request)
    assert validate_region_budget(plan) == ()
    assert validate_region_budget(replace(plan, budget=replace(plan.budget, max_cost_micros=None))) == (
        "bpmn_region_finite_cost_budget_required",
    )
    child = next(node for node in plan.nodes if node.node_type == "task")
    changed = replace(child, budget=replace(plan.budget, max_cost_micros=plan.budget.max_cost_micros + 1))
    expanded = replace(plan, nodes=tuple(changed if node.node_id == child.node_id else node for node in plan.nodes))
    assert validate_region_budget(expanded) == ("bpmn_region_budget_escalation",)


@pytest.mark.parametrize("limit", ["max_tokens", "max_cost_micros"])
def test_xml_loop_finite_run_budget_failure_stops_later_iterations_and_survives_restart(monkeypatch, limit):
    from tests.bpmn.test_execution_recovery import restart

    hub, queue, handler, keys, ledger, stores, assignment = xml_runtime(monkeypatch)
    bounded = replace(assignment.plan.budget, max_tokens=10, max_cost_micros=10)
    bounded = replace(bounded, **{limit: 1})
    assignment = replace(assignment, plan=replace(assignment.plan, budget=bounded))
    result = advance_until(hub, assignment, hub.start(assignment), lambda value: value.status != "running")
    assert result.status == "failed"
    assert result.reason_code.startswith("native_budget_exceeded:")
    assert len(queue.submissions) == 2
    assert "again" not in result.completed_node_ids
    resumed = restart(queue, keys, ledger, stores).advance(assignment)
    assert resumed.status == "failed"
    assert len(queue.submissions) == 2


def test_xml_loop_run_deadline_survives_restart_during_an_iteration(monkeypatch):
    from tests.bpmn.test_execution_recovery import restart

    hub, queue, handler, keys, ledger, stores, assignment = xml_runtime(monkeypatch)
    bounded = replace(assignment.plan.budget, timeout_seconds=1.0, max_tokens=10, max_cost_micros=10)
    assignment = replace(assignment, plan=replace(assignment.plan, budget=bounded))
    result = advance_until(hub, assignment, hub.start(assignment), lambda _: len(queue.submissions) == 1)
    assert result.checkpoint.state.runtime_metadata["bpmn_deadline_at"] == 101.0
    result = restart(queue, keys, ledger, stores, clock=lambda: 102.0).advance(assignment)
    assert result.status == "failed"
    assert result.reason_code == "bpmn_run_deadline_exceeded"
    assert len(queue.submissions) == 1


def test_subprocess_native_runtime_scopes_the_delegated_payload():
    from agent.services.native_graph_orchestration_service import NativeGraphRequest
    from tests.bpmn.test_execution_routing import execution_plan
    from tests.test_native_graph_runtime import runtime

    _, request = compile_xml(subprocess_xml())
    plan = execution_plan(request)
    assert plan.validate() == ()
    hub, queue, handler, *_ = runtime()
    assignment = NativeGraphRequest(
        plan, "synthetic-region", "control", input_data={"public": 7, "unrelated": "private"}
    )
    result = hub.start(assignment)
    for _ in range(30):
        if result.status != "running":
            break
        result = hub.advance(assignment)
    assert result.status == "completed", result.reason_code
    assert len(handler.calls) == 1
    assert queue.submissions[0].input_data["workflow_input"] == {"value": 7}
    assert queue.submissions[0].input_data["dependency_results"] == {}


@pytest.mark.parametrize("value,maximum,expected", [(3, 3, 0), (0, 3, 3), (0, 2, 2), (2, 3, 1)])
def test_native_loop_entry_repeat_early_exit_and_maximum(monkeypatch, value, maximum, expected):
    from agent.services.native_graph_orchestration_service import NativeGraphRequest
    from tests.bpmn.test_execution_routing import execution_plan
    from tests.test_native_graph_runtime import runtime

    _, request = compile_xml(loop_xml(maximum=maximum))
    plan = execution_plan(request)
    assert plan.validate() == ()
    hub, queue, handler, *_ = runtime()
    original = handler.execute

    def increment(command, *, hub_task_id):
        answer = original(command, hub_task_id=hub_task_id)
        return replace(answer, output_data={"value": command.input_data["workflow_input"]["value"] + 1})

    monkeypatch.setattr(handler, "execute", increment)
    assignment = NativeGraphRequest(
        plan, "synthetic-loop", "control", input_data={"value": value, "unrelated": "private"}
    )
    result = hub.start(assignment)
    for _ in range(100):
        if result.status != "running":
            break
        result = hub.advance(assignment)
    assert result.status == "completed", result.reason_code
    assert len(handler.calls) == expected
    assert all(set(command.input_data["workflow_input"]) == {"value"} for command in queue.submissions)
    assert result.checkpoint.state.business_data["node_results"]["again"] == {"value": value + expected}
