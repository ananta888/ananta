"""BPMN import/export adapter for Ananta visual process graphs.

The adapter deliberately stays inside the hub-side visual-process boundary:
it translates BPMN XML into the canonical ``VisualProcessGraph`` contract and
back. It does not execute workflows or make worker-routing decisions.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from agent.visual_process.models import (
    ArtifactRef,
    StepIOContract,
    StepPosition,
    TransitionCondition,
    VisualProcessEdge,
    VisualProcessGraph,
    VisualProcessStep,
)


BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"
DI_NS = "http://www.omg.org/spec/DD/20100524/DI"
ANANTA_NS = "https://ananta.local/bpmn"
NS = {
    "bpmn": BPMN_NS,
    "bpmndi": BPMNDI_NS,
    "dc": DC_NS,
    "di": DI_NS,
    "ananta": ANANTA_NS,
}

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)


STEP_TAG_KIND = {
    f"{{{BPMN_NS}}}startEvent": "start",
    f"{{{BPMN_NS}}}endEvent": "end",
    f"{{{BPMN_NS}}}task": "task",
    f"{{{BPMN_NS}}}serviceTask": "tool_task",
    f"{{{BPMN_NS}}}userTask": "human_task",
    f"{{{BPMN_NS}}}scriptTask": "coding",
    f"{{{BPMN_NS}}}businessRuleTask": "review",
    f"{{{BPMN_NS}}}exclusiveGateway": "decision",
    f"{{{BPMN_NS}}}parallelGateway": "parallel",
}
KIND_BPMN_TAG = {
    "start": "startEvent",
    "end": "endEvent",
    "tool_task": "serviceTask",
    "service_task": "serviceTask",
    "human_task": "userTask",
    "manual": "userTask",
    "coding": "scriptTask",
    "review": "businessRuleTask",
    "decision": "exclusiveGateway",
    "parallel": "parallelGateway",
}


@dataclass
class BpmnConversionResult:
    graph: VisualProcessGraph | None = None
    bpmn_xml: str | None = None
    warnings: list[str] = field(default_factory=list)


def import_bpmn_xml(bpmn_xml: str) -> BpmnConversionResult:
    """Convert BPMN XML into a ``VisualProcessGraph``.

    Unknown BPMN elements are ignored with warnings so imported diagrams can be
    incrementally normalized instead of rejected wholesale.
    """
    warnings: list[str] = []
    try:
        root = ET.fromstring(bpmn_xml)
    except ET.ParseError as exc:
        raise ValueError(f"invalid BPMN XML: {exc}") from exc

    process = root.find("bpmn:process", NS)
    if process is None:
        raise ValueError("BPMN XML does not contain a bpmn:process element")

    graph_name = process.attrib.get("name") or root.attrib.get("name") or "BPMN Blueprint"
    graph = VisualProcessGraph(
        id=_clean_id(process.attrib.get("id") or "bpmn-process"),
        name=graph_name,
        description=_process_description(process),
        metadata={
            "source_format": "bpmn",
            "bpmn_process_id": process.attrib.get("id", ""),
        },
    )

    positions = _read_positions(root)
    for element in list(process):
        if element.tag not in STEP_TAG_KIND:
            if element.tag.endswith("sequenceFlow"):
                continue
            if _local_name(element.tag) not in {"extensionElements", "documentation"}:
                warnings.append(f"Unsupported BPMN element ignored: {_local_name(element.tag)}")
            continue
        metadata = _read_ananta_metadata(element)
        io = _io_from_metadata(metadata)
        step_id = _clean_id(element.attrib.get("id") or f"step-{len(graph.steps) + 1}")
        kind = str(metadata.get("kind") or STEP_TAG_KIND[element.tag])
        graph.steps.append(
            VisualProcessStep(
                id=step_id,
                label=element.attrib.get("name") or step_id,
                kind=kind,
                role=_optional_str(metadata.get("role")),
                agent_skill_profile_id=_optional_str(metadata.get("agent_skill_profile_id")),
                io=io,
                position=positions.get(step_id, StepPosition()),
                policy_hints=[str(item) for item in metadata.get("policy_hints", []) if str(item).strip()],
                gate=bool(metadata.get("gate", kind == "human_task")),
                metadata={
                    **{k: v for k, v in metadata.items() if k not in {"io", "policy_hints"}},
                    "bpmn_element_type": _local_name(element.tag),
                },
            )
        )

    for flow in process.findall("bpmn:sequenceFlow", NS):
        source = _clean_id(flow.attrib.get("sourceRef") or "")
        target = _clean_id(flow.attrib.get("targetRef") or "")
        if not source or not target:
            warnings.append(f"Sequence flow {flow.attrib.get('id', '')} is missing sourceRef or targetRef")
            continue
        if graph.step_by_id(source) is None or graph.step_by_id(target) is None:
            warnings.append(f"Sequence flow {flow.attrib.get('id', '')} references an unsupported element")
            continue
        metadata = _read_ananta_metadata(flow)
        edge_id = _clean_id(flow.attrib.get("id") or f"edge-{source}-{target}")
        condition = _condition_from_metadata(flow, metadata)
        graph.edges.append(
            VisualProcessEdge(
                id=edge_id,
                source=source,
                target=target,
                label=flow.attrib.get("name") or None,
                condition=condition,
                metadata=metadata,
            )
        )

    return BpmnConversionResult(graph=graph, warnings=warnings)


def export_bpmn_xml(graph: VisualProcessGraph) -> BpmnConversionResult:
    """Convert an Ananta visual process graph into BPMN XML for bpmn-js."""
    warnings: list[str] = []
    definitions = ET.Element(
        _q("bpmn", "definitions"),
        {
            "id": f"Definitions_{_xml_id(graph.id)}",
            "targetNamespace": "https://ananta.local/workflows",
            "name": graph.name,
        },
    )
    process = ET.SubElement(
        definitions,
        _q("bpmn", "process"),
        {"id": _xml_id(graph.id), "name": graph.name, "isExecutable": "false"},
    )
    if graph.description:
        documentation = ET.SubElement(process, _q("bpmn", "documentation"))
        documentation.text = graph.description

    for step in graph.steps:
        tag = KIND_BPMN_TAG.get(step.kind, "serviceTask")
        if tag == "serviceTask" and step.kind not in {"tool_task", "service_task"}:
            warnings.append(f"Step {step.id} kind '{step.kind}' exported as serviceTask")
        element = ET.SubElement(
            process,
            _q("bpmn", tag),
            {"id": _xml_id(step.id), "name": step.label or step.id},
        )
        _append_ananta_metadata(
            element,
            {
                "kind": step.kind,
                "role": step.role,
                "agent_skill_profile_id": step.agent_skill_profile_id,
                "policy_hints": step.policy_hints,
                "gate": step.gate,
                "io": step.io.model_dump(),
                **(step.metadata or {}),
            },
        )

    for edge in graph.edges:
        attrs = {
            "id": _xml_id(edge.id),
            "sourceRef": _xml_id(edge.source),
            "targetRef": _xml_id(edge.target),
        }
        if edge.label:
            attrs["name"] = edge.label
        flow = ET.SubElement(process, _q("bpmn", "sequenceFlow"), attrs)
        if edge.condition.kind in {"expression", "on_output"}:
            expression = edge.condition.expression or edge.condition.output_name or ""
            expr = ET.SubElement(flow, _q("bpmn", "conditionExpression"), {"type": "ananta"})
            expr.text = expression
        _append_ananta_metadata(flow, {"condition": edge.condition.model_dump(), **(edge.metadata or {})})

    _append_diagram(definitions, graph)
    return BpmnConversionResult(
        bpmn_xml=ET.tostring(definitions, encoding="unicode", xml_declaration=True),
        warnings=warnings,
    )


def _read_positions(root: ET.Element) -> dict[str, StepPosition]:
    positions: dict[str, StepPosition] = {}
    for shape in root.findall(".//bpmndi:BPMNShape", NS):
        element_id = _clean_id(shape.attrib.get("bpmnElement") or "")
        bounds = shape.find("dc:Bounds", NS)
        if not element_id or bounds is None:
            continue
        positions[element_id] = StepPosition(
            x=float(bounds.attrib.get("x") or 0),
            y=float(bounds.attrib.get("y") or 0),
        )
    return positions


def _append_diagram(definitions: ET.Element, graph: VisualProcessGraph) -> None:
    diagram = ET.SubElement(definitions, _q("bpmndi", "BPMNDiagram"), {"id": f"Diagram_{_xml_id(graph.id)}"})
    plane = ET.SubElement(
        diagram,
        _q("bpmndi", "BPMNPlane"),
        {"id": f"Plane_{_xml_id(graph.id)}", "bpmnElement": _xml_id(graph.id)},
    )
    for index, step in enumerate(graph.steps):
        x = step.position.x if step.position.x else 160 + index * 180
        y = step.position.y if step.position.y else 160
        width, height = (36, 36) if step.kind in {"start", "end"} else (120, 80)
        shape = ET.SubElement(
            plane,
            _q("bpmndi", "BPMNShape"),
            {"id": f"Shape_{_xml_id(step.id)}", "bpmnElement": _xml_id(step.id)},
        )
        ET.SubElement(
            shape,
            _q("dc", "Bounds"),
            {"x": str(x), "y": str(y), "width": str(width), "height": str(height)},
        )
    for edge in graph.edges:
        bpmn_edge = ET.SubElement(
            plane,
            _q("bpmndi", "BPMNEdge"),
            {"id": f"Edge_{_xml_id(edge.id)}", "bpmnElement": _xml_id(edge.id)},
        )
        source = graph.step_by_id(edge.source)
        target = graph.step_by_id(edge.target)
        if source and target:
            ET.SubElement(
                bpmn_edge,
                _q("di", "waypoint"),
                {"x": str(source.position.x + 120), "y": str(source.position.y + 40)},
            )
            ET.SubElement(
                bpmn_edge,
                _q("di", "waypoint"),
                {"x": str(target.position.x), "y": str(target.position.y + 40)},
            )


def _read_ananta_metadata(element: ET.Element) -> dict[str, Any]:
    extension = element.find("bpmn:extensionElements", NS)
    if extension is None:
        return {}
    metadata = extension.find("ananta:metadata", NS)
    if metadata is None or not metadata.text:
        return {}
    try:
        value = json.loads(metadata.text)
    except json.JSONDecodeError:
        return {"raw_metadata": metadata.text}
    return value if isinstance(value, dict) else {"raw_metadata": value}


def _append_ananta_metadata(element: ET.Element, metadata: dict[str, Any]) -> None:
    clean = {k: v for k, v in metadata.items() if v not in (None, "", [], {})}
    if not clean:
        return
    extension = ET.SubElement(element, _q("bpmn", "extensionElements"))
    meta = ET.SubElement(extension, _q("ananta", "metadata"))
    meta.text = json.dumps(clean, sort_keys=True)


def _condition_from_metadata(flow: ET.Element, metadata: dict[str, Any]) -> TransitionCondition:
    condition = metadata.get("condition")
    if isinstance(condition, dict):
        try:
            return TransitionCondition.model_validate(condition)
        except Exception:
            pass
    expression = flow.find("bpmn:conditionExpression", NS)
    if expression is not None and expression.text and expression.text.strip():
        return TransitionCondition(kind="expression", expression=expression.text.strip())
    return TransitionCondition()


def _io_from_metadata(metadata: dict[str, Any]) -> StepIOContract:
    io = metadata.get("io")
    if isinstance(io, dict):
        try:
            return StepIOContract.model_validate(io)
        except Exception:
            return StepIOContract()
    inputs = [ArtifactRef(name=str(item)) for item in metadata.get("inputs", []) if str(item).strip()]
    outputs = [ArtifactRef(name=str(item)) for item in metadata.get("outputs", []) if str(item).strip()]
    return StepIOContract(inputs=inputs, outputs=outputs)


def _process_description(process: ET.Element) -> str:
    documentation = process.find("bpmn:documentation", NS)
    return (documentation.text or "").strip() if documentation is not None else ""


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _q(prefix: str, name: str) -> str:
    return f"{{{NS[prefix]}}}{name}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clean_id(value: str) -> str:
    return str(value or "").strip()


def _xml_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "").strip())
    if not cleaned:
        return "id"
    if not re.match(r"^[A-Za-z_]", cleaned):
        return f"id_{cleaned}"
    return cleaned
