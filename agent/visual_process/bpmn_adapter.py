"""BPMN import/export adapter for Ananta visual process graphs.

The adapter deliberately stays inside the hub-side visual-process boundary:
it translates BPMN XML into the canonical ``VisualProcessGraph`` contract and
back. It does not execute workflows or make worker-routing decisions.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from agent.visual_process.bpmn_execution_support import (
    SOURCE_KEY,
    assert_bpmn_source_supported,
    execution_report,
    parse_ananta_metadata,
    parse_bpmn,
)
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
    f"{{{BPMN_NS}}}intermediateCatchEvent": "bpmn_wait",
    f"{{{BPMN_NS}}}subProcess": "task",
}
KIND_BPMN_TAG = {
    "task": "task",
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
    "bpmn_wait": "intermediateCatchEvent",
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
    root = parse_bpmn(bpmn_xml)
    report = execution_report(root)
    source_sha256 = hashlib.sha256(bpmn_xml.encode("utf-8")).hexdigest()
    from agent.visual_process.bpmn_xml_regions import expand_xml_regions, has_xml_regions

    region_manifest = None
    if report["supported"] and has_xml_regions(root):
        root, region_manifest = expand_xml_regions(root, source_sha256=source_sha256)

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
            "bpmn_process_metadata": _read_ananta_metadata(process),
            SOURCE_KEY: bpmn_xml,
            "bpmn_execution_report": report,
            "bpmn_source_sha256": source_sha256,
            **({"bpmn_xml_regions": region_manifest} if region_manifest is not None else {}),
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
        if element.tag == f"{{{BPMN_NS}}}intermediateCatchEvent" and report["supported"]:
            from agent.visual_process.bpmn_event_definitions import parse_event_definition

            metadata["bpmn_wait"] = parse_event_definition(element, root)
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
                policy_hints=_metadata_strings(metadata, "policy_hints"),
                gate=bool(metadata.get("gate", kind == "human_task")),
                metadata={
                    **{k: v for k, v in metadata.items() if k not in {"io", "policy_hints"}},
                    "bpmn_element_type": _local_name(element.tag),
                    "bpmn_default_flow": element.get("default", ""),
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
        try:
            condition = _condition_from_metadata(flow, metadata)
        except ValueError:
            # The source support report is authoritative for admission. Keep a
            # lossy preview editable without interpreting invalid expressions.
            warnings.append(f"Condition of {edge_id} is not representable; execution is blocked")
            condition = TransitionCondition()
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
    # A normalized graph cannot reconstruct ignored XML. Keep the original XML
    # editable in the modeler, but never export a silently reduced definition.
    assert_bpmn_source_supported(graph)
    if graph.metadata.get("bpmn_xml_regions") or any(step.kind == "bpmn_wait" for step in graph.steps):
        from agent.visual_process.bpmn_execution_compiler import compile_bpmn_graph

        compile_bpmn_graph(graph)
        # Flattened node IDs and synthetic controls are execution projections,
        # never a replacement for the editable structured source document.
        return BpmnConversionResult(bpmn_xml=graph.metadata[SOURCE_KEY])
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
    if graph.metadata.get("bpmn_process_metadata"):
        _append_ananta_metadata(process, graph.metadata["bpmn_process_metadata"])

    for step in graph.steps:
        tag = step.metadata.get("bpmn_element_type") or KIND_BPMN_TAG.get(step.kind, "serviceTask")
        if tag == "serviceTask" and step.kind not in {"tool_task", "service_task"}:
            warnings.append(f"Step {step.id} kind '{step.kind}' exported as serviceTask")
        element = ET.SubElement(
            process,
            _q("bpmn", tag),
            {"id": _xml_id(step.id), "name": step.label or step.id},
        )
        if tag == "exclusiveGateway" and step.metadata.get("bpmn_default_flow"):
            element.set("default", _xml_id(step.metadata["bpmn_default_flow"]))
        _append_ananta_metadata(
            element,
            {
                **(step.metadata or {}),
                "kind": step.kind,
                **({"role": step.role} if step.role is not None else {}),
                **(
                    {"agent_skill_profile_id": step.agent_skill_profile_id}
                    if step.agent_skill_profile_id is not None
                    else {}
                ),
                "policy_hints": step.policy_hints,
                "gate": step.gate,
                "io": step.io.model_dump(),
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
            expr = ET.SubElement(flow, _q("bpmn", "conditionExpression"), {"language": "ananta-condition-v1"})
            expr.text = expression
        _append_ananta_metadata(flow, {**(edge.metadata or {}), "condition": edge.condition.model_dump()})

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
        value = parse_ananta_metadata(metadata.text)
    except (ValueError, TypeError, RecursionError):
        return {"raw_metadata": metadata.text}
    return value if isinstance(value, dict) else {"raw_metadata": value}


def _append_ananta_metadata(element: ET.Element, metadata: dict[str, Any]) -> None:
    if not metadata:
        return
    extension = ET.SubElement(element, _q("bpmn", "extensionElements"))
    meta = ET.SubElement(extension, _q("ananta", "metadata"))
    meta.text = json.dumps(metadata, sort_keys=True, allow_nan=False)


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
    inputs = [ArtifactRef(name=item) for item in _metadata_strings(metadata, "inputs")]
    outputs = [ArtifactRef(name=item) for item in _metadata_strings(metadata, "outputs")]
    return StepIOContract(inputs=inputs, outputs=outputs)


def _metadata_strings(metadata, key):
    value = metadata.get(key)
    return [str(item) for item in value if str(item).strip()] if isinstance(value, list) else []


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
