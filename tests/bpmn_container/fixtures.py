"""Minimal synthetic BPMN definitions owned by this acceptance suite."""

import json
from html import escape


def metadata(value):
    return (
        '<extensionElements><metadata xmlns="https://ananta.local/bpmn">'
        + escape(json.dumps(value))
        + "</metadata></extensionElements>"
    )


def projection():
    return {
        "schema": "ananta.bpmn_input_projection.v1",
        "workflow_input": {"value": ["input", "value"]},
        "dependency_results": {},
    }


def loop():
    return service().replace(
        '<serviceTask id="work"/>',
        '<serviceTask id="work">'
        + metadata(
            {
                "bpmn_input_projection": projection(),
                "bpmn_loop": {
                    "schema": "ananta.bpmn_loop.v1",
                    "input_mapping": {"value": ["input", "value"]},
                    "repeat_mapping": {"value": ["results", "work", "value"]},
                },
            }
        )
        + '<standardLoopCharacteristics testBefore="true" loopMaximum="3">'
        "<loopCondition>input.value &lt; 3</loopCondition></standardLoopCharacteristics></serviceTask>",
    )


def subprocess():
    return service().replace(
        '<serviceTask id="work"/>',
        '<subProcess id="work">'
        + metadata(
            {
                "bpmn_region": {
                    "schema": "ananta.bpmn_region.v1",
                    "input_mapping": {"value": ["input", "value"]},
                    "output_mapping": {"value": ["results", "child", "value"]},
                }
            }
        )
        + '<startEvent id="child_start"/><serviceTask id="child">'
        + metadata({"bpmn_input_projection": projection()})
        + '</serviceTask><endEvent id="child_end"/>'
        '<sequenceFlow id="child_enter" sourceRef="child_start" targetRef="child"/>'
        '<sequenceFlow id="child_leave" sourceRef="child" targetRef="child_end"/></subProcess>',
    )


def catch(kind):
    options = {"timeout_seconds": 20}
    declaration = ""
    event = "<timerEventDefinition><timeDuration>PT0S</timeDuration></timerEventDefinition>"
    if kind == "message":
        options.update(
            correlation_key="synthetic-order",
            payload_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        )
        declaration = '<message id="order" name="order.received"/>'
        event = '<messageEventDefinition messageRef="order"/>'
    xml = service().replace(
        '<serviceTask id="work"/>',
        '<intermediateCatchEvent id="catch">'
        + metadata({"bpmn_wait": options})
        + event
        + '</intermediateCatchEvent><serviceTask id="work"/>',
    )
    xml = xml.replace('id="begin" sourceRef="start" targetRef="work"', 'id="begin" sourceRef="start" targetRef="catch"')
    return xml.replace("<process ", declaration + "<process ", 1).replace(
        "</process>", '<sequenceFlow id="caught" sourceRef="catch" targetRef="work"/></process>'
    )


def diagram(body: str) -> str:
    return (
        '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" '
        'targetNamespace="urn:ananta:bpmn-container-test"><process id="core">' + body + "</process></definitions>"
    )


def xor(*, default: bool = False) -> str:
    fallback = ' default="no"' if default else ""
    condition = "" if default else "<conditionExpression>input.approved == False</conditionExpression>"
    return diagram(
        '<startEvent id="start"/>' + f'<exclusiveGateway id="choose"{fallback}/>'
        '<serviceTask id="yes_task"/><serviceTask id="no_task"/>'
        '<exclusiveGateway id="join"/><endEvent id="end"/>'
        '<sequenceFlow id="begin" sourceRef="start" targetRef="choose"/>'
        '<sequenceFlow id="yes" sourceRef="choose" targetRef="yes_task">'
        "<conditionExpression>input.approved == True</conditionExpression></sequenceFlow>"
        f'<sequenceFlow id="no" sourceRef="choose" targetRef="no_task">{condition}</sequenceFlow>'
        '<sequenceFlow id="yes_join" sourceRef="yes_task" targetRef="join"/>'
        '<sequenceFlow id="no_join" sourceRef="no_task" targetRef="join"/>'
        '<sequenceFlow id="finish" sourceRef="join" targetRef="end"/>'
    )


def parallel() -> str:
    nodes = [
        ("startEvent", "start"),
        ("parallelGateway", "split"),
        ("serviceTask", "fast"),
        ("serviceTask", "slow"),
        ("parallelGateway", "join"),
        ("serviceTask", "after"),
        ("endEvent", "end"),
    ]
    edges = [
        ("start", "split"),
        ("split", "fast"),
        ("split", "slow"),
        ("fast", "join"),
        ("slow", "join"),
        ("join", "after"),
        ("after", "end"),
    ]
    return diagram(
        "".join(f'<{kind} id="{name}"/>' for kind, name in nodes)
        + "".join(f'<sequenceFlow id="e{i}" sourceRef="{a}" targetRef="{b}"/>' for i, (a, b) in enumerate(edges))
    )


def approval() -> str:
    return diagram(
        '<startEvent id="start"/><userTask id="review"/><endEvent id="end"/>'
        '<sequenceFlow id="begin" sourceRef="start" targetRef="review"/>'
        '<sequenceFlow id="finish" sourceRef="review" targetRef="end"/>'
    )


def service() -> str:
    return diagram(
        '<startEvent id="start"/><serviceTask id="work"/><endEvent id="end"/>'
        '<sequenceFlow id="begin" sourceRef="start" targetRef="work"/>'
        '<sequenceFlow id="finish" sourceRef="work" targetRef="end"/>'
    )


def artifact() -> str:
    from agent.visual_process.bpmn_execution_support import ANANTA_NS

    return (
        service()
        .replace('id="work"', 'id="artifact_work"')
        .replace('targetRef="work"', 'targetRef="artifact_work"')
        .replace('sourceRef="work"', 'sourceRef="artifact_work"')
        .replace(
            '<serviceTask id="artifact_work"/>',
            '<serviceTask id="artifact_work"><extensionElements>'
            f'<ananta:metadata xmlns:ananta="{ANANTA_NS}">'
            + '{"outputs":["report"]}</ananta:metadata></extensionElements></serviceTask>',
        )
    )
