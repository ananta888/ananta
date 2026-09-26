"""Nested AND activations cannot mix arrivals between bounded XML iterations."""

import xml.etree.ElementTree as ET

from agent.visual_process.bpmn_execution_support import parse_bpmn
from agent.visual_process.bpmn_xml_region_contracts import ORIGIN_KEY, projection, set_metadata, tag
from tests.bpmn.test_xml_regions import looped_subprocess_xml, xml_runtime


def nested_parallel_loop():
    root = parse_bpmn(looped_subprocess_xml())
    region = root.find(f".//{tag('subProcess')}")
    for edge in region.findall(tag("sequenceFlow")):
        region.remove(edge)
    for kind, identity in (
        ("parallelGateway", "split"),
        ("parallelGateway", "inner_split"),
        ("parallelGateway", "inner_join"),
        ("parallelGateway", "join"),
        ("serviceTask", "left"),
        ("serviceTask", "delayed"),
    ):
        node = ET.SubElement(region, tag(kind), id=identity)
        set_metadata(node, {"bpmn_input_projection": projection({"value": ["input", "value"]})})
    edges = (
        ("inner_start", "split"),
        ("split", "work"),
        ("split", "inner_split"),
        ("inner_split", "left"),
        ("inner_split", "delayed"),
        ("left", "inner_join"),
        ("delayed", "inner_join"),
        ("inner_join", "join"),
        ("work", "join"),
        ("join", "inner_end"),
    )
    for index, (source, target) in enumerate(edges):
        ET.SubElement(region, tag("sequenceFlow"), id=f"parallel_{index}", sourceRef=source, targetRef=target)
    return ET.tostring(root, encoding="unicode")


def test_nested_and_waits_for_its_own_arrivals_in_every_iteration(monkeypatch):
    hub, queue, handler, *_, request = xml_runtime(monkeypatch, xml=nested_parallel_loop())
    poll = queue.poll
    deferred = []
    delayed_iterations = set()

    def delayed_poll(**values):
        results = [*deferred, *poll(**values)]
        deferred.clear()
        ready = []
        commands = {command.node.node_id: command for command in queue.submissions}
        for result in results:
            origin = commands[result.node_id].node.metadata[ORIGIN_KEY]
            iteration = next(scope["iteration"] for scope in origin["scope"] if "iteration" in scope)
            if origin["source_id"] == "delayed" and iteration not in delayed_iterations:
                delayed_iterations.add(iteration)
                deferred.append(result)
            else:
                ready.append(result)
        return tuple(ready)

    monkeypatch.setattr(queue, "poll", delayed_poll)
    result = hub.start(request)
    for _ in range(180):
        if result.status != "running":
            break
        result = hub.advance(request)
        for pending in deferred:
            origin = next(node for node in request.plan.nodes if node.node_id == pending.node_id).metadata[ORIGIN_KEY]
            joins = [
                node.node_id
                for node in request.plan.nodes
                if node.metadata[ORIGIN_KEY]["source_id"] in {"inner_join", "join"}
                and node.metadata[ORIGIN_KEY]["scope"] == origin["scope"]
            ]
            assert joins and not set(joins) & set(result.completed_node_ids)
    assert result.status == "completed", result.reason_code
    assert delayed_iterations == {0, 1, 2}
    assert len(queue.submissions) == len(set(handler.calls)) == 9
    assert result.checkpoint.state.business_data["node_results"]["region"] == {"value": 3}
