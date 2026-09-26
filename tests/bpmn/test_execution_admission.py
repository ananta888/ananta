"""Deterministic, synthetic BPMN regression cases; no live services or evidence."""

from __future__ import annotations

import pytest

from agent.visual_process.blueprint_mapper import graph_to_workflow_request
from agent.visual_process.bpmn_adapter import import_bpmn_xml


def diagram(body: str, *, attributes: str = "") -> str:
    return (
        '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" '
        'id="definitions" targetNamespace="urn:test:bpmn">'
        f'<process id="process" {attributes}>{body}</process></definitions>'
    )


@pytest.mark.parametrize(
    "extra",
    [
        '<startEvent id="start"><timerEventDefinition><timeDuration>PT1S</timeDuration>'
        "</timerEventDefinition></startEvent>",
        '<boundaryEvent id="boundary" attachedToRef="task"/>',
        '<subProcess id="sub"><task id="nested"/></subProcess>',
        '<serviceTask id="loop"><multiInstanceLoopCharacteristics/></serviceTask>',
        '<scriptTask id="script"><script>do_not_execute()</script></scriptTask>',
    ],
    ids=["timer-definition", "boundary", "subprocess", "multi-instance", "script"],
)
def test_unsupported_semantics_cannot_be_erased_by_import(extra):
    graph = import_bpmn_xml(diagram('<task id="task"/>' + extra)).graph
    with pytest.raises(ValueError, match="bpmn"):
        graph_to_workflow_request(graph, policy_scope={"source": "synthetic-test"})


def test_removed_support_report_does_not_authorize_original_unsupported_xml():
    graph = import_bpmn_xml(diagram('<task id="task"/><boundaryEvent id="b"/>')).graph
    graph.metadata.pop("bpmn_execution_report", None)
    with pytest.raises(ValueError, match="bpmn"):
        graph_to_workflow_request(graph, policy_scope={"source": "synthetic-test"})


@pytest.mark.parametrize(
    "xml",
    [
        '<!DOCTYPE definitions [<!ENTITY payload "unsafe">]>' + diagram('<task id="a"/>'),
        diagram('<task id="a"/>') + " " * (1024 * 1024),
        diagram('<task id="same"/><task id="same"/>'),
    ],
    ids=["entity", "size", "duplicate-id"],
)
def test_bounded_parser_rejects_entities_size_and_duplicate_ids(xml):
    with pytest.raises(ValueError):
        import_bpmn_xml(xml)


def test_nested_xml_has_a_bounded_depth():
    xml = diagram("<extensionElements>" * 70 + "</extensionElements>" * 70)
    with pytest.raises(ValueError, match="depth"):
        import_bpmn_xml(xml)


def test_simple_bpmn_remains_compilable():
    graph = import_bpmn_xml(
        diagram(
            '<startEvent id="start"/><serviceTask id="task"/><endEvent id="end"/>'
            '<sequenceFlow id="first" sourceRef="start" targetRef="task"/>'
            '<sequenceFlow id="last" sourceRef="task" targetRef="end"/>'
        )
    ).graph
    request = graph_to_workflow_request(graph, policy_scope={"source": "synthetic-test"})
    assert request.validate() == []
    assert request.steps[1].depends_on == ("start",)
