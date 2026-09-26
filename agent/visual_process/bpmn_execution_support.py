"""Bounded XML inspection and executable-subset admission, without I/O.

An import is editable, not an authorization. Execution re-inspects original XML;
neither client warnings nor a client-supplied 'supported' flag are authority.
"""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.visual_process.models import VisualProcessGraph

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
ANANTA_NS = "https://ananta.local/bpmn"
SOURCE_KEY = "bpmn_source_xml"
MAX_XML_BYTES = 1024 * 1024
MAX_XML_ELEMENTS = 4096
MAX_XML_DEPTH = 64
SUPPORTED_ELEMENTS = frozenset(
    {
        "startEvent",
        "endEvent",
        "task",
        "serviceTask",
        "userTask",
        "exclusiveGateway",
        "parallelGateway",
        "sequenceFlow",
        "subProcess",
        "intermediateCatchEvent",
    }
)
_BASE_ATTRIBUTES = {"id", "name"}
_ATTRIBUTES = {
    "process": {"isExecutable"},
    "sequenceFlow": {"sourceRef", "targetRef"},
    "exclusiveGateway": {"default", "gatewayDirection"},
    "parallelGateway": {"gatewayDirection"},
    "serviceTask": {"implementation"},
    "standardLoopCharacteristics": {"testBefore", "loopMaximum"},
}
_CHILDREN = {
    "process": SUPPORTED_ELEMENTS | {"documentation", "extensionElements"},
    "sequenceFlow": {"documentation", "extensionElements", "conditionExpression"},
    "incoming": set(),
    "outgoing": set(),
    "conditionExpression": set(),
    "loopCondition": set(),
    "standardLoopCharacteristics": {"loopCondition"},
    "subProcess": SUPPORTED_ELEMENTS
    | {"documentation", "extensionElements", "incoming", "outgoing", "standardLoopCharacteristics"},
}
for _activity in ("task", "serviceTask", "userTask"):
    _CHILDREN[_activity] = {"documentation", "extensionElements", "incoming", "outgoing", "standardLoopCharacteristics"}


@dataclass(frozen=True)
class BpmnExecutionIssue:
    reason_code: str
    element_id: str
    detail: str


class BpmnExecutionError(ValueError):
    def __init__(self, issues):
        self.issues = tuple(issues)
        super().__init__("bpmn_not_executable:" + ",".join(issue.reason_code for issue in self.issues))

    def as_dict(self):
        return {"error": "bpmn_not_executable", "issues": [asdict(issue) for issue in self.issues]}


def parse_bpmn(xml: str) -> ET.Element:
    if not isinstance(xml, str) or len(xml.encode("utf-8")) > MAX_XML_BYTES:
        raise ValueError("bpmn_xml_size_limit")
    if "\x00" in xml or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.IGNORECASE):
        raise ValueError("bpmn_xml_entities_forbidden")
    parser = ET.XMLPullParser(events=("start", "end"))
    depth = count = 0
    root = None
    ids = set()
    try:
        for offset in range(0, len(xml), 4096):
            parser.feed(xml[offset : offset + 4096])
            for event, element in parser.read_events():
                if event == "end":
                    depth -= 1
                    continue
                depth += 1
                count += 1
                if root is None:
                    root = element
                if depth > MAX_XML_DEPTH:
                    raise ValueError("bpmn_xml_depth_limit")
                if count > MAX_XML_ELEMENTS:
                    raise ValueError("bpmn_xml_element_limit")
                identity = element.get("id")
                if identity:
                    if identity in ids:
                        raise ValueError("bpmn_duplicate_element_id")
                    ids.add(identity)
        parser.close()
    except ET.ParseError as exc:
        raise ValueError("bpmn_xml_invalid") from exc
    if root is None or root.tag != f"{{{BPMN_NS}}}definitions":
        raise ValueError("bpmn_definitions_required")
    return root


def inspect_bpmn(root: ET.Element) -> tuple[BpmnExecutionIssue, ...]:
    issues = []
    for key, value in root.attrib.items():
        if key == "expressionLanguage" and value == "ananta-condition-v1":
            continue
        if key not in {"id", "name", "targetNamespace", "exporter", "exporterVersion"}:
            issues.append(BpmnExecutionIssue("bpmn_definition_attribute_unsupported", root.get("id", ""), key))
    processes = root.findall(f"{{{BPMN_NS}}}process")
    if len(processes) != 1:
        issues.append(BpmnExecutionIssue("bpmn_single_process_required", "", "Exactly one process is executable."))
    for child in root:
        if child.tag == f"{{{BPMN_NS}}}message":
            if set(child.attrib) - {"id", "name"} or not child.get("id") or len(child):
                issues.append(
                    BpmnExecutionIssue(
                        "bpmn_message_declaration_unsupported", child.get("id", ""), "Closed message declarations only."
                    )
                )
            continue
        if child.tag not in {f"{{{BPMN_NS}}}process", f"{{{BPMN_NS}}}documentation"} and not child.tag.startswith(
            "{http://www.omg.org/spec/BPMN/20100524/DI}"
        ):
            issues.append(BpmnExecutionIssue("bpmn_definition_unsupported", child.get("id", ""), child.tag))
    for process in processes:
        _inspect_element(process, issues, process.get("id", ""), root)
    for process in [
        node
        for process in processes
        for node in process.iter()
        if node.tag in {f"{{{BPMN_NS}}}process", f"{{{BPMN_NS}}}subProcess"}
    ]:
        nodes = {
            child.get("id")
            for child in process
            if child.tag in {f"{{{BPMN_NS}}}{name}" for name in SUPPORTED_ELEMENTS - {"sequenceFlow"}}
        }
        for flow in process.findall(f"{{{BPMN_NS}}}sequenceFlow"):
            if flow.get("sourceRef") not in nodes or flow.get("targetRef") not in nodes:
                issues.append(
                    BpmnExecutionIssue(
                        "bpmn_flow_reference_invalid",
                        flow.get("id", ""),
                        "Both ends must reference supported process nodes.",
                    )
                )
        flows = process.findall(f"{{{BPMN_NS}}}sequenceFlow")
        for node in process:
            for direction, reference in (("incoming", "targetRef"), ("outgoing", "sourceRef")):
                declared = node.findall(f"{{{BPMN_NS}}}{direction}")
                if declared and {(item.text or "").strip() for item in declared} != {
                    flow.get("id") for flow in flows if flow.get(reference) == node.get("id")
                }:
                    issues.append(BpmnExecutionIssue("bpmn_flow_reference_mismatch", node.get("id", ""), direction))
    if not issues:
        from agent.visual_process.bpmn_xml_regions import expand_xml_regions, has_xml_regions

        if has_xml_regions(root):
            try:
                # Structural validation only. Import binds the exact original
                # UTF-8 source hash; this tree is never exported or executed.
                expand_xml_regions(root, source_sha256="0" * 64)
            except BpmnExecutionError as exc:
                issues.extend(exc.issues)
    return tuple(issues)


def _inspect_element(element, issues, owner, root=None):
    local = element.tag.rsplit("}", 1)[-1]
    identity = element.get("id", owner)
    if element.tag == f"{{{BPMN_NS}}}intermediateCatchEvent":
        _inspect_catch_event(element, issues, identity, root)
        return
    if element.tag == f"{{{BPMN_NS}}}documentation":
        return
    if element.tag == f"{{{BPMN_NS}}}extensionElements":
        if len(element) != 1 or element.attrib:
            issues.append(
                BpmnExecutionIssue(
                    "bpmn_extension_unsupported", owner, "Exactly one Ananta metadata extension is supported."
                )
            )
        for child in element:
            if child.tag != f"{{{ANANTA_NS}}}metadata" or len(child) or child.attrib:
                issues.append(BpmnExecutionIssue("bpmn_extension_unsupported", owner, child.tag))
                continue
            try:
                value = parse_ananta_metadata(child.text or "")
                if {
                    "bpmn_region_control",
                    "bpmn_projection_join",
                    "bpmn_activation_origin",
                    "bpmn_control",
                }.intersection(value):
                    issues.append(
                        BpmnExecutionIssue(
                            "bpmn_region_metadata_reserved", owner, "Compiler-owned metadata cannot be supplied by XML."
                        )
                    )
            except (ValueError, RecursionError):
                issues.append(BpmnExecutionIssue("bpmn_metadata_invalid", owner, "Metadata must be a JSON object."))
        return
    if local not in SUPPORTED_ELEMENTS | {
        "process",
        "incoming",
        "outgoing",
        "conditionExpression",
        "standardLoopCharacteristics",
        "loopCondition",
    } or not element.tag.startswith(f"{{{BPMN_NS}}}"):
        issues.append(BpmnExecutionIssue("bpmn_element_unsupported", identity, local))
        return
    allowed = _BASE_ATTRIBUTES | _ATTRIBUTES.get(local, set())
    if local in {"conditionExpression", "loopCondition"}:
        from agent.visual_process.bpmn_conditions import compile_condition

        allowed = {"type", "{http://www.w3.org/2001/XMLSchema-instance}type", "language"}
        try:
            compile_condition((element.text or "").strip(), element_id=owner)
        except BpmnExecutionError as exc:
            issues.extend(exc.issues)
        if element.get("language", "ananta-condition-v1") != "ananta-condition-v1":
            issues.append(
                BpmnExecutionIssue(
                    "bpmn_expression_language_unsupported", owner, "Only ananta-condition-v1 is supported."
                )
            )
        if not (element.text or "").strip():
            issues.append(
                BpmnExecutionIssue("bpmn_expression_required", owner, "Empty conditions are not unconditional flows.")
            )
        for attribute in ("type", "{http://www.w3.org/2001/XMLSchema-instance}type"):
            if attribute in element.attrib and element.attrib[attribute].split(":")[-1] not in {
                "ananta",
                "tFormalExpression",
            }:
                issues.append(BpmnExecutionIssue("bpmn_expression_type_unsupported", owner, attribute))
    if local in SUPPORTED_ELEMENTS and not element.get("id"):
        issues.append(BpmnExecutionIssue("bpmn_element_id_required", owner, local))
    for key, value in element.attrib.items():
        if key not in allowed or (key == "implementation" and value not in {"", "##unspecified"}):
            issues.append(BpmnExecutionIssue("bpmn_attribute_unsupported", identity, key))
        if key == "gatewayDirection" and value not in {"Unspecified", "Converging", "Diverging"}:
            issues.append(BpmnExecutionIssue("bpmn_gateway_direction_unsupported", identity, value))
    permitted = _CHILDREN.get(local, {"documentation", "extensionElements", "incoming", "outgoing"})
    for name in ("extensionElements", "conditionExpression", "standardLoopCharacteristics", "loopCondition"):
        if len(element.findall(f"{{{BPMN_NS}}}{name}")) > 1:
            issues.append(BpmnExecutionIssue("bpmn_duplicate_semantics", identity, name))
    for child in element:
        if child.tag not in {f"{{{BPMN_NS}}}{name}" for name in permitted}:
            issues.append(BpmnExecutionIssue("bpmn_child_unsupported", identity, child.tag))
            continue
        _inspect_element(child, issues, identity, root)
    if local == "sequenceFlow":
        metadata_element = element.find(f"{{{BPMN_NS}}}extensionElements/{{{ANANTA_NS}}}metadata")
        expression = element.find(f"{{{BPMN_NS}}}conditionExpression")
        if metadata_element is not None and expression is not None:
            try:
                metadata = parse_ananta_metadata(metadata_element.text or "{}")
                condition = metadata.get("condition")
                if condition is not None and (
                    not isinstance(condition, dict)
                    or condition.get("kind") != "expression"
                    or condition.get("expression", "").strip() != (expression.text or "").strip()
                ):
                    issues.append(
                        BpmnExecutionIssue(
                            "bpmn_condition_binding_mismatch",
                            identity,
                            "XML expression and extension condition must agree.",
                        )
                    )
            except (ValueError, TypeError, AttributeError, RecursionError):
                pass  # Invalid metadata was already recorded by the extension inspector.


def _inspect_catch_event(element, issues, identity, root):
    from agent.visual_process.bpmn_event_definitions import parse_event_definition

    try:
        parse_event_definition(element, root)
    except (ValueError, TypeError, RecursionError) as exc:
        issues.append(BpmnExecutionIssue(str(exc), identity, "Unsupported intermediate catch definition."))
    for child in element:
        if child.tag in {
            f"{{{BPMN_NS}}}{name}" for name in ("incoming", "outgoing", "documentation", "extensionElements")
        }:
            _inspect_element(child, issues, identity, root)


def parse_ananta_metadata(text):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate_metadata_key")
            value[key] = item
        return value

    value = json.loads(text, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("metadata_object_required")
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 16 or count > 4096:
            raise ValueError("metadata_complexity_limit")
        if type(item) is float and not math.isfinite(item):
            raise ValueError("metadata_finite_number_required")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    for key in ("kind", "role", "agent_skill_profile_id"):
        if key in value and not isinstance(value[key], str):
            raise ValueError(key)
    if "gate" in value and type(value["gate"]) is not bool:
        raise ValueError("gate")
    for key in ("policy_scope", "policyScope"):
        if key in value and not isinstance(value[key], dict):
            raise ValueError(key)
    for key in ("allowed_tools", "policy_hints", "inputs", "outputs"):
        if key in value and (not isinstance(value[key], list) or any(not isinstance(item, str) for item in value[key])):
            raise ValueError(key)
    if "io" in value:
        from agent.visual_process.models import StepIOContract

        StepIOContract.model_validate(value["io"])
    if "condition" in value:
        from agent.visual_process.models import TransitionCondition

        TransitionCondition.model_validate(value["condition"])
    return value


def execution_report(root):
    issues = inspect_bpmn(root)
    return {
        "schema": "ananta.bpmn_execution_report.v1",
        "supported": not issues,
        "runtime_verified": False,
        "issues": [asdict(issue) for issue in issues],
    }


def has_bpmn_source_metadata(metadata) -> bool:
    return metadata.get("source_format") == "bpmn" or bool(
        {SOURCE_KEY, "bpmn_process_id", "bpmn_process_metadata", "bpmn_execution_report"}.intersection(metadata)
    )


def assert_bpmn_source_supported(graph: VisualProcessGraph) -> None:
    source = graph.metadata.get(SOURCE_KEY)
    is_bpmn = has_bpmn_source_metadata(graph.metadata) or any(
        "bpmn_element_type" in step.metadata for step in graph.steps
    )
    if not source and not is_bpmn:
        return
    if not isinstance(source, str) or not source:
        raise BpmnExecutionError(
            [BpmnExecutionIssue("bpmn_source_required", graph.id, "Re-import the original BPMN XML.")]
        )
    try:
        issues = inspect_bpmn(parse_bpmn(source))
    except ValueError as exc:
        raise BpmnExecutionError([BpmnExecutionIssue("bpmn_source_invalid", graph.id, str(exc))]) from exc
    if issues:
        raise BpmnExecutionError(issues)


def capability_catalog():
    return {
        "schema": "ananta.bpmn_capabilities.v1",
        "version": "1.0",
        "runtime_verified": False,
        "expression_language": "ananta-condition-v1",
        "required_capability": "bpmn_control_v1",
        "required_feature_flag": "ANANTA_BPMN_EXECUTION_ENABLED",
        "runtimes": {
            "ananta-native": "opt_in_contract",
            "temporal": "unsupported",
            "langgraph": "unsupported",
            "legacy": "unsupported",
        },
        "elements": [
            {"element": name, "modelable": True, "execution_contract_supported": True}
            for name in sorted(SUPPORTED_ELEMENTS)
        ],
        "unsupported": [
            "scriptTask",
            "businessRuleTask",
            "boundaryEvent",
            "callActivity",
            "inclusiveGateway",
            "eventBasedGateway",
            "multiInstanceLoopCharacteristics",
        ],
        "bounded_regions": {
            "standard_loop": "testBefore=true, finite loopMaximum <= 32, explicit input/repeat mapping",
            "subprocess": "single start/end, depth <= 8, explicit input/output and child projections",
            "callActivity": "unsupported",
        },
    }
