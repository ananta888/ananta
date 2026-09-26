"""Independent synthetic BPMN regressions; no services or release evidence.

Run in the isolated backend image with --confcutdir=tests/bpmn. These tests
assert the required safe behavior and intentionally fail while findings remain.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import replace

import pytest

from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.workflow_backend import WorkflowRequest
from agent.services.workflow_runtime.security import SignedCheckpoint
from agent.visual_process.blueprint_mapper import graph_to_blueprint_dict, graph_to_workflow_request
from agent.visual_process.bpmn_adapter import export_bpmn_xml, import_bpmn_xml
from agent.visual_process.bpmn_execution_support import BpmnExecutionError
from tests.bpmn.test_execution_admission import diagram
from tests.bpmn.test_execution_recovery import advance_bounded, restart
from tests.bpmn.test_execution_routing import compile_request, execution_plan, xor_xml
from tests.test_native_graph_runtime import runtime


def _extension(metadata: dict) -> str:
    return (
        '<extensionElements><metadata xmlns="https://ananta.local/bpmn">'
        + json.dumps(metadata)
        + "</metadata></extensionElements>"
    )


def _linear_xml(*, task_metadata: dict | None = None, process_metadata: dict | None = None) -> str:
    return diagram(
        (_extension(process_metadata) if process_metadata is not None else "")
        + '<startEvent id="s"/><serviceTask id="work">'
        + (_extension(task_metadata) if task_metadata is not None else "")
        + '</serviceTask><endEvent id="end"/>'
        '<sequenceFlow id="a" sourceRef="s" targetRef="work"/>'
        '<sequenceFlow id="b" sourceRef="work" targetRef="end"/>'
    )


@pytest.mark.parametrize("boundary", ["graph", "request"])
def test_xml_declared_service_task_gate_cannot_be_removed_without_rebinding(boundary):
    xml = _linear_xml(task_metadata={"gate": True})
    original = compile_request(xml)
    hub, queue, *_ = runtime()
    baseline = NativeGraphRequest(execution_plan(original), "baseline-gate", "control")
    assert advance_bounded(hub, baseline, hub.start(baseline)).status == "waiting_for_approval"
    assert queue.submissions == []

    if boundary == "graph":
        graph = import_bpmn_xml(xml).graph
        graph.step_by_id("work").gate = False
        try:
            changed = graph_to_workflow_request(graph, policy_scope={"source": "synthetic-test"})
        except BpmnExecutionError:
            return  # Rejecting a stale source binding is a safe outcome.
    else:
        changed = replace(
            original,
            steps=tuple(replace(step, gate=False) if step.step_id == "work" else step for step in original.steps),
        )
    if changed.validate():
        return
    hub, queue, handler, *_ = runtime()
    assignment = NativeGraphRequest(execution_plan(changed), "changed-gate", "control")
    result = advance_bounded(hub, assignment, hub.start(assignment))
    assert queue.submissions == [], (
        "Unchanged XML still requires approval, but the modified projection "
        f"delegated {handler.calls} and ended {result.status}"
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"policy_scope": {"source": "synthetic-test"}, "review_tag": "retain-this"},
        {"review_tag": "retain-this", "unset_value": None, "empty_list": [], "empty_map": {}, "empty_text": ""},
    ],
)
def test_admitted_process_extension_survives_normalized_xml_export(metadata):
    xml = _linear_xml(process_metadata=metadata)
    try:
        compile_request(xml)
    except BpmnExecutionError:
        return  # An unsupported extension may instead be explicitly rejected.
    restored = ET.fromstring(export_bpmn_xml(import_bpmn_xml(xml).graph).bpmn_xml)
    extension = restored.find(
        "{http://www.omg.org/spec/BPMN/20100524/MODEL}process/"
        "{http://www.omg.org/spec/BPMN/20100524/MODEL}extensionElements/"
        "{https://ananta.local/bpmn}metadata"
    )
    assert extension is not None, "Executable process metadata was silently dropped during export"
    assert json.loads(extension.text) == metadata


@pytest.mark.parametrize("expression", ["input.flags == [True]", "input.flags in [[True]]"])
def test_collection_comparisons_do_not_treat_integer_as_boolean(expression):
    xml = xor_xml(default=True).replace("input.approved == True", expression)
    try:
        request = compile_request(xml)
    except BpmnExecutionError:
        return  # The declared expression dialect is scalar-only.
    hub, queue, handler, *_ = runtime()
    assignment = NativeGraphRequest(execution_plan(request), "typed-condition", "control", input_data={"flags": [1]})
    result = advance_bounded(hub, assignment, hub.start(assignment))
    assert "yes_task" not in handler.calls, (
        f"{expression} selected the True branch for [1]: {result.status}; "
        f"delegations={[command.node.node_id for command in queue.submissions]}"
    )


@pytest.mark.parametrize("metadata", [{"policy_hints": None}, {"inputs": None}, {"outputs": 1}])
def test_invalid_metadata_import_returns_client_error_instead_of_crashing(metadata):
    from flask import Flask

    from agent.routes import visual_process as routes

    app = Flask(__name__)
    app.testing = True
    app.add_url_rule("/import", view_func=routes.bpmn_import, methods=["POST"])
    response = app.test_client().post("/import", json={"bpmn_xml": _linear_xml(task_metadata=metadata)})
    payload = response.get_json()
    assert response.status_code in {400, 422} or (
        response.status_code == 200 and payload["execution_support"]["supported"] is False
    ), payload


def test_direct_request_admission_handles_invalid_xml_metadata_without_crashing():
    original = compile_request(_linear_xml())
    raw = original.to_dict()
    raw["metadata"]["bpmn_source_xml"] = _linear_xml(task_metadata={"policy_hints": None})
    changed = WorkflowRequest.from_mapping(raw)
    assert changed.validate(), "Malformed XML metadata must produce admission errors"


def test_retained_unsupported_xml_cannot_bypass_legacy_blueprint_guard_by_removing_markers():
    xml = _linear_xml().replace(
        '<startEvent id="s"/>',
        '<startEvent id="s"><timerEventDefinition/></startEvent>',
    )
    graph = import_bpmn_xml(xml).graph
    assert graph.metadata["bpmn_execution_report"]["supported"] is False
    graph.metadata.pop("source_format")
    for step in graph.steps:
        step.metadata.pop("bpmn_element_type")
    assert graph.metadata["bpmn_source_xml"] == xml
    with pytest.raises(BpmnExecutionError):
        graph_to_blueprint_dict(graph)


@pytest.mark.parametrize(
    "raw",
    [
        '{"gate":true,"gate":false}',
        '{"extra":NaN}',
        '{"extra":' + "[" * 17 + "0" + "]" * 17 + "}",
        '{"extra":[' + ",".join("0" for _ in range(4097)) + "]}",
    ],
    ids=["duplicate-key", "nonfinite", "depth-limit", "node-limit"],
)
def test_rejected_metadata_remains_previewable_but_not_executable(raw):
    xml = _linear_xml(task_metadata={}).replace("{}", raw)
    graph = import_bpmn_xml(xml).graph
    assert graph.metadata["bpmn_execution_report"]["supported"] is False
    assert graph.step_by_id("work").metadata["raw_metadata"] == raw
    with pytest.raises(BpmnExecutionError):
        graph_to_workflow_request(graph, policy_scope={"source": "synthetic-test"})


def test_flow_metadata_recursion_does_not_escape_structured_admission():
    raw = '{"extra":' + "[" * 1200 + "0" + "]" * 1200 + "}"
    xml = xor_xml().replace(
        "<conditionExpression>input.approved == True</conditionExpression>",
        "<conditionExpression>input.approved == True</conditionExpression>"
        '<extensionElements><metadata xmlns="https://ananta.local/bpmn">' + raw + "</metadata></extensionElements>",
    )
    try:
        graph = import_bpmn_xml(xml).graph
    except ValueError:
        return  # The HTTP import boundary converts ValueError to a client error.
    assert graph.metadata["bpmn_execution_report"]["supported"] is False
    with pytest.raises(BpmnExecutionError):
        graph_to_workflow_request(graph, policy_scope={"source": "synthetic-test"})


def _deadline_request(xml: str) -> NativeGraphRequest:
    plan = execution_plan(compile_request(xml))
    return NativeGraphRequest(
        replace(plan, budget=replace(plan.budget, timeout_seconds=10.0)),
        "deadline-review",
        "control",
    )


def test_approval_wait_expires_at_exact_persisted_deadline_after_restart():
    hub, queue, _, keys, ledger, stores = runtime()
    request = _deadline_request(_linear_xml().replace("serviceTask", "userTask"))
    waiting = advance_bounded(hub, request, hub.start(request))
    assert waiting.status == "waiting_for_approval"
    assert waiting.checkpoint.state.runtime_metadata["bpmn_deadline_at"] == 110.0
    before = restart(queue, keys, ledger, stores, clock=lambda: 109.0).advance(request)
    assert before.status == "waiting_for_approval"
    resumed = restart(queue, keys, ledger, stores, clock=lambda: 110.0)
    expired = resumed.advance(request)
    assert expired.status == "failed"
    assert expired.reason_code == "bpmn_run_deadline_exceeded"
    assert queue.submissions == []
    assert resumed.advance(request).checkpoint.revision == expired.checkpoint.revision


def test_deadline_cancels_running_assignment_before_accepting_late_result():
    hub, queue, _, keys, ledger, stores = runtime()
    request = _deadline_request(_linear_xml())
    hub.start(request)
    pending = hub.advance(request)
    assert pending.status == "running"
    assert len(queue.submissions) == 1
    expired = restart(queue, keys, ledger, stores, clock=lambda: 110.0).advance(request)
    assert expired.status == "failed"
    assert expired.reason_code == "bpmn_run_deadline_exceeded"
    assert "work" not in expired.completed_node_ids
    assert queue.cancelled == ["hub-task-1"]


def test_deadline_crossed_during_queue_submit_prevents_next_batch_delegation(monkeypatch):
    xml = diagram(
        '<startEvent id="s"/><parallelGateway id="fork"/>'
        '<serviceTask id="a"/><serviceTask id="b"/>'
        '<parallelGateway id="join"/><endEvent id="end"/>'
        '<sequenceFlow id="f0" sourceRef="s" targetRef="fork"/>'
        '<sequenceFlow id="f1" sourceRef="fork" targetRef="a"/>'
        '<sequenceFlow id="f2" sourceRef="fork" targetRef="b"/>'
        '<sequenceFlow id="f3" sourceRef="a" targetRef="join"/>'
        '<sequenceFlow id="f4" sourceRef="b" targetRef="join"/>'
        '<sequenceFlow id="f5" sourceRef="join" targetRef="end"/>'
    )
    _, queue, _, keys, ledger, stores = runtime()
    now = [100.0]
    hub = restart(queue, keys, ledger, stores, clock=lambda: now[0])
    request = _deadline_request(xml)
    original_submit = queue.submit

    def slow_submit(command):
        receipt = original_submit(command)
        now[0] = 111.0  # Deterministic queue latency; no sleeping or live service.
        return receipt

    monkeypatch.setattr(queue, "submit", slow_submit)
    hub.start(request)
    hub.advance(request)  # Complete the split.
    result = hub.advance(request)
    assert [command.node.node_id for command in queue.submissions] == ["a"], (
        f"The next task was submitted after the persisted deadline expired: {result.status}; clock={now[0]}"
    )


@pytest.mark.parametrize(
    "deadline_fields",
    [
        {},
        {"bpmn_started_at": 100.0, "bpmn_deadline_at": 999.0},
        {"bpmn_started_at": 100.0, "bpmn_deadline_at": "110.0"},
        {"bpmn_started_at": False, "bpmn_deadline_at": 10.0},
    ],
    ids=["missing", "extended", "wrong-type", "boolean-start"],
)
def test_signed_checkpoint_with_invalid_deadline_binding_is_rejected(deadline_fields):
    hub, queue, _, keys, _, stores = runtime()
    request = _deadline_request(_linear_xml())
    checkpoint = hub.start(request).checkpoint
    metadata = dict(checkpoint.state.runtime_metadata)
    metadata.pop("bpmn_started_at")
    metadata.pop("bpmn_deadline_at")
    metadata.update(deadline_fields)
    # A synthetic older/defective Hub issuer can sign incomplete state. Exercise
    # semantic validation separately from the already-tested HMAC tamper check.
    defective = SignedCheckpoint.issue(
        key_ring=keys,
        tenant_id=checkpoint.tenant_id,
        workflow_id=checkpoint.workflow_id,
        run_id=checkpoint.run_id,
        task_id=checkpoint.task_id,
        plan_hash=checkpoint.plan_hash,
        policy_version=checkpoint.policy_version,
        runtime_id=checkpoint.runtime_id,
        runtime_version=checkpoint.runtime_version,
        state=replace(checkpoint.state, runtime_metadata=metadata),
        revision=checkpoint.revision + 1,
        fencing_token=checkpoint.fencing_token,
        now=100.0,
    )
    stores["checkpoints"].save(defective, expected_revision=checkpoint.revision)
    with pytest.raises(ValueError, match="bpmn_deadline_binding"):
        hub.advance(request)
    assert queue.submissions == []
