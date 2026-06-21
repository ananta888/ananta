from __future__ import annotations


def _simple_bpmn() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
                  id="Defs_1"
                  targetNamespace="https://ananta.local/workflows">
  <bpmn:process id="process_1" name="BPMN Test" isExecutable="false">
    <bpmn:startEvent id="start" name="Start" />
    <bpmn:serviceTask id="task_a" name="Analyse" />
    <bpmn:userTask id="approval" name="Freigabe" />
    <bpmn:endEvent id="end" name="Ende" />
    <bpmn:sequenceFlow id="flow_1" sourceRef="start" targetRef="task_a" />
    <bpmn:sequenceFlow id="flow_2" sourceRef="task_a" targetRef="approval" />
    <bpmn:sequenceFlow id="flow_3" sourceRef="approval" targetRef="end" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="Diagram_1">
    <bpmndi:BPMNPlane id="Plane_1" bpmnElement="process_1">
      <bpmndi:BPMNShape id="Shape_task_a" bpmnElement="task_a">
        <dc:Bounds x="220" y="120" width="120" height="80" />
      </bpmndi:BPMNShape>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>"""


def test_import_bpmn_xml_maps_process_to_visual_graph():
    from agent.visual_process.bpmn_adapter import import_bpmn_xml

    result = import_bpmn_xml(_simple_bpmn())

    assert result.graph is not None
    assert result.graph.name == "BPMN Test"
    assert [step.id for step in result.graph.steps] == ["start", "task_a", "approval", "end"]
    assert result.graph.step_by_id("task_a").kind == "tool_task"
    assert result.graph.step_by_id("approval").gate is True
    assert [(edge.source, edge.target) for edge in result.graph.edges] == [
        ("start", "task_a"),
        ("task_a", "approval"),
        ("approval", "end"),
    ]


def test_export_bpmn_xml_roundtrips_visual_graph():
    from agent.visual_process.bpmn_adapter import export_bpmn_xml, import_bpmn_xml

    graph = import_bpmn_xml(_simple_bpmn()).graph
    exported = export_bpmn_xml(graph)
    imported = import_bpmn_xml(exported.bpmn_xml)

    assert "<bpmn:process" in exported.bpmn_xml
    assert imported.graph.name == graph.name
    assert {step.id for step in imported.graph.steps} == {step.id for step in graph.steps}
    assert {edge.id for edge in imported.graph.edges} == {edge.id for edge in graph.edges}


def test_graph_to_workflow_request_requires_policy_scope():
    from agent.visual_process.bpmn_adapter import import_bpmn_xml
    from agent.visual_process.blueprint_mapper import graph_to_workflow_request

    graph = import_bpmn_xml(_simple_bpmn()).graph

    missing_policy = graph_to_workflow_request(graph, workflow_type="bpmn")
    assert any(error.startswith("policy_scope_required:") for error in missing_policy.validate())

    workflow = graph_to_workflow_request(
        graph,
        workflow_type="bpmn",
        policy_scope={"source": "test"},
        allowed_tools=["read_file"],
    )
    assert workflow.validate() == []
    assert workflow.steps[2].gate is True
    assert workflow.steps[1].depends_on == ("start",)


def test_local_workflow_backend_models_start_signal_and_cancel():
    from agent.services.local_workflow_backend import LocalWorkflowBackend
    from agent.services.workflow_backend import WorkflowSignal
    from agent.visual_process.bpmn_adapter import import_bpmn_xml
    from agent.visual_process.blueprint_mapper import graph_to_workflow_request

    graph = import_bpmn_xml(_simple_bpmn()).graph
    workflow = graph_to_workflow_request(graph, policy_scope={"source": "test"})
    backend = LocalWorkflowBackend()

    started = backend.start_workflow(workflow)
    assert started["status"] == "waiting_for_approval"
    assert started["backend"] == "local"

    approved = backend.signal_workflow(workflow.workflow_id, WorkflowSignal(name="approve", actor="tester"))
    assert approved["status"] == "running"
    assert any(event["event_type"] == "signal:approve" for event in approved["events"])

    cancelled = backend.cancel_workflow(workflow.workflow_id, "test done")
    assert cancelled["status"] == "cancelled"


def test_local_workflow_backend_exposes_active_running_step():
    from agent.services.local_workflow_backend import LocalWorkflowBackend
    from agent.visual_process.models import VisualProcessGraph, VisualProcessStep
    from agent.visual_process.blueprint_mapper import graph_to_workflow_request

    graph = VisualProcessGraph(
        id="wf-active",
        name="Active workflow",
        steps=[
            VisualProcessStep(id="plan", label="Plan", kind="goal_plan"),
            VisualProcessStep(id="build", label="Build", kind="coding"),
        ],
    )
    workflow = graph_to_workflow_request(graph, policy_scope={"source": "test"})
    status = LocalWorkflowBackend().start_workflow(workflow)

    assert status["status"] == "running"
    assert status["steps"][0]["status"] == "running"
    assert any(event["event_type"] == "step_started" for event in status["events"])


def test_visual_process_bpmn_and_workflow_routes():
    from flask import Flask
    from agent.routes.visual_process import vp_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(vp_bp)
    client = app.test_client()

    imported = client.post("/api/visual-process/bpmn/import", json={"bpmn_xml": _simple_bpmn()})
    assert imported.status_code == 200
    graph = imported.get_json()["graph"]

    compiled = client.post(
        "/api/visual-process/workflow-request",
        json={"graph": graph, "policy_scope": {"source": "route-test"}},
    )
    assert compiled.status_code == 200
    payload = compiled.get_json()
    assert payload["workflow_request"]["schema"] == "ananta.workflow_request.v1"

    started = client.post(
        "/api/visual-process/workflow/start",
        json={"workflow_request": payload["workflow_request"]},
    )
    assert started.status_code == 200
    assert started.get_json()["backend"] == "local"
