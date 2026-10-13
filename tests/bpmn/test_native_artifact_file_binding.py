"""BPMN file declarations/IO remain source-bound through compilation and loading."""

import copy

import pytest

from agent.services.workflow_backend import WorkflowRequest
from agent.visual_process.blueprint_mapper import graph_to_workflow_request
from agent.visual_process.bpmn_adapter import export_bpmn_xml, import_bpmn_xml
from agent.visual_process.bpmn_execution_compiler import compile_bpmn_graph
from tests.bpmn.test_xml_regions import document, meta


def artifact_document(output_path="outputs/report.txt"):
    return document(
        '<startEvent id="start"/><serviceTask id="producer">' + meta({
            "outputs": ["report"], "native_output_files": {"report": {"path": output_path, "media_type": "text/plain"}},
        }) + '</serviceTask><serviceTask id="consumer">' + meta({
            "inputs": ["report"], "native_input_files": {"report": "inputs/report.txt"},
        }) + '</serviceTask><endEvent id="end"/>'
        '<sequenceFlow id="begin" sourceRef="start" targetRef="producer"/>'
        '<sequenceFlow id="handoff" sourceRef="producer" targetRef="consumer"/>'
        '<sequenceFlow id="finish" sourceRef="consumer" targetRef="end"/>'
    )


def test_declarations_survive_xml_and_request_roundtrips():
    graph = import_bpmn_xml(artifact_document()).graph
    reimported = import_bpmn_xml(export_bpmn_xml(graph).bpmn_xml).graph
    for current in (graph, reimported):
        projection = compile_bpmn_graph(current)
        assert projection["node_metadata"]["producer"]["native_output_files"]["report"]["path"] == "outputs/report.txt"
        assert projection["node_metadata"]["consumer"]["native_input_files"] == {"report": "inputs/report.txt"}
        request = graph_to_workflow_request(current, policy_scope={"source": "synthetic-test"})
        assert WorkflowRequest.from_mapping(request.to_dict()).validate() == []


@pytest.mark.parametrize("kind", ["file-path", "input-io", "output-io"])
def test_editable_graph_cannot_override_bound_xml_without_reimport(kind):
    graph = import_bpmn_xml(artifact_document()).graph
    if kind == "file-path":
        graph.step_by_id("producer").metadata["native_output_files"]["report"]["path"] = "different.txt"
    elif kind == "input-io":
        graph.step_by_id("consumer").io.inputs = []
    else:
        graph.step_by_id("producer").io.outputs = []
    with pytest.raises(ValueError, match="bpmn_source_binding_mismatch"):
        compile_bpmn_graph(graph)


@pytest.mark.parametrize("kind", ["file-path", "input-io", "output-io"])
def test_request_loading_rejects_source_bound_file_and_io_edits(kind):
    request = graph_to_workflow_request(import_bpmn_xml(artifact_document()).graph)
    raw = copy.deepcopy(request.to_dict())
    producer = next(step for step in raw["steps"] if step["step_id"] == "producer")
    consumer = next(step for step in raw["steps"] if step["step_id"] == "consumer")
    if kind == "file-path":
        producer["metadata"]["native_output_files"]["report"]["path"] = "different.txt"
        expected = "bpmn_step_metadata_binding_mismatch"
    else:
        step = consumer if kind == "input-io" else producer
        step["input_artifacts" if kind == "input-io" else "output_artifacts"] = []
        expected = "bpmn_artifact_binding_mismatch"
    assert expected in WorkflowRequest.from_mapping(raw).validate()


@pytest.mark.parametrize("path", ["../outside", "/etc/passwd", "output/../report", "output//report"])
def test_unsafe_xml_file_paths_are_not_compilable(path):
    with pytest.raises(ValueError, match="native_artifact_workspace_path_invalid"):
        compile_bpmn_graph(import_bpmn_xml(artifact_document(path)).graph)
