from __future__ import annotations

from typing import Any

from agent.services.notation_renderer_common import (
    NotationRenderError,
    _as_dict_entries,
    _as_list,
    _as_str,
    _check_identifier,
)

_BPMN_NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

_BPMN_TARGET_NS = "http://bpmn.io/schema/bpmn"

_VALID_FLOW_ELEMENT_TYPES = {
    "startEvent", "endEvent", "intermediateThrowEvent", "intermediateCatchEvent",
    "userTask", "serviceTask", "scriptTask", "manualTask", "task",
    "exclusiveGateway", "inclusiveGateway", "parallelGateway", "eventBasedGateway",
}


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_bpmn_element(elem: dict) -> str:
    etype = _as_str(elem.get("type"), field="elements[].type")
    if etype not in _VALID_FLOW_ELEMENT_TYPES:
        raise NotationRenderError(
            f"element type {etype!r} must be one of "
            f"{sorted(_VALID_FLOW_ELEMENT_TYPES)}"
        )
    eid = _as_str(elem.get("id"), field="elements[].id")
    _check_identifier(eid, field="elements[].id")
    name = elem.get("name")
    attrs = f' id="{_xml_escape(eid)}"'
    if isinstance(name, str) and name:
        attrs += f' name="{_xml_escape(name)}"'
    return f"    <bpmn:{etype}{attrs} />"


def _render_bpmn_flow(flow: dict) -> str:
    fid = _as_str(flow.get("id"), field="flows[].id")
    _check_identifier(fid, field="flows[].id")
    src = _as_str(flow.get("sourceRef"), field="flows[].sourceRef")
    _check_identifier(src, field="flows[].sourceRef")
    tgt = _as_str(flow.get("targetRef"), field="flows[].targetRef")
    _check_identifier(tgt, field="flows[].targetRef")
    name = flow.get("name")
    cond = flow.get("conditionExpression")
    body = ""
    if isinstance(cond, str) and cond.strip():
        body = (
            f'<bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">'
            f"{_xml_escape(cond.strip())}</bpmn:conditionExpression>"
        )
    attrs = (
        f' id="{_xml_escape(fid)}"'
        f' sourceRef="{_xml_escape(src)}"'
        f' targetRef="{_xml_escape(tgt)}"'
    )
    if isinstance(name, str) and name:
        attrs += f' name="{_xml_escape(name)}"'
    return f"    <bpmn:sequenceFlow{attrs}>{body}</bpmn:sequenceFlow>"


def _render_diagram_shape(elem_id: str, *, x: int, y: int) -> str:
    return (
        f'      <bpmndi:BPMNShape id="{_xml_escape(elem_id)}_di" '
        f'bpmnElement="{_xml_escape(elem_id)}">\n'
        f'        <dc:Bounds x="{x}" y="{y}" width="100" height="80" />\n'
        f'      </bpmndi:BPMNShape>'
    )


def _render_diagram_edge(flow_id: str) -> str:
    return (
        f'      <bpmndi:BPMNEdge id="{_xml_escape(flow_id)}_di" '
        f'bpmnElement="{_xml_escape(flow_id)}">\n'
        f'        <di:waypoint x="150" y="120" />\n'
        f'        <di:waypoint x="280" y="120" />\n'
        f'      </bpmndi:BPMNEdge>'
    )


def _definitions_open(definitions_id: str) -> str:
    ns_attrs = " ".join(f'xmlns:{k}="{v}"' for k, v in _BPMN_NS.items())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<bpmn:definitions id="{_xml_escape(definitions_id)}" {ns_attrs} '
        f'targetNamespace="{_BPMN_TARGET_NS}">'
    )


def _validate_flow_elements(elements: list[dict]) -> list[str]:
    seen: set[str] = set()
    start_count = 0
    element_ids: list[str] = []
    for elem in elements:
        etype = _as_str(elem.get("type"), field="elements[].type")
        eid = _as_str(elem.get("id"), field="elements[].id")
        _check_identifier(eid, field="elements[].id")
        if eid in seen:
            raise NotationRenderError(f"duplicate element id {eid!r}")
        seen.add(eid)
        element_ids.append(eid)
        if etype not in _VALID_FLOW_ELEMENT_TYPES:
            raise NotationRenderError(
                f"element type {etype!r} must be one of "
                f"{sorted(_VALID_FLOW_ELEMENT_TYPES)}"
            )
        if etype == "startEvent":
            start_count += 1
    if start_count != 1:
        raise NotationRenderError(
            f"process must contain exactly one startEvent, found {start_count}"
        )
    return element_ids


def _validate_flows(flows: list[dict], known_ids: set[str]) -> None:
    seen_flow_ids: set[str] = set()
    for flow in flows:
        fid = _as_str(flow.get("id"), field="flows[].id")
        _check_identifier(fid, field="flows[].id")
        if fid in seen_flow_ids:
            raise NotationRenderError(f"duplicate flow id {fid!r}")
        seen_flow_ids.add(fid)
        src = _as_str(flow.get("sourceRef"), field="flows[].sourceRef")
        tgt = _as_str(flow.get("targetRef"), field="flows[].targetRef")
        if src not in known_ids:
            raise NotationRenderError(
                f"flow {fid!r} sourceRef {src!r} references unknown element"
            )
        if tgt not in known_ids:
            raise NotationRenderError(
                f"flow {fid!r} targetRef {tgt!r} references unknown element"
            )


def _render_bpmn_process(params: dict[str, Any]) -> tuple[str, str]:
    definitions_id = _as_str(params.get("definitions_id"), field="definitions_id")
    _check_identifier(definitions_id, field="definitions_id")
    process_id = _as_str(params.get("process_id"), field="process_id")
    _check_identifier(process_id, field="process_id")
    process_name = params.get("process_name") or ""
    elements_raw = _as_list(params.get("elements"), field="elements")
    if not elements_raw:
        raise NotationRenderError("elements must be a non-empty list")
    elements = _as_dict_entries(elements_raw, field="elements")
    flows_raw = _as_list(params.get("flows"), field="flows")
    flows = _as_dict_entries(flows_raw, field="flows")

    element_ids = _validate_flow_elements(elements)
    _validate_flows(flows, set(element_ids))

    lines: list[str] = [_definitions_open(definitions_id)]
    proc_open = f'  <bpmn:process id="{_xml_escape(process_id)}" isExecutable="true"'
    if isinstance(process_name, str) and process_name:
        proc_open += f' name="{_xml_escape(process_name)}"'
    proc_open += ">"
    lines.append(proc_open)
    for elem in elements:
        lines.append(_render_bpmn_element(elem))
    for flow in flows:
        lines.append(_render_bpmn_flow(flow))
    lines.append("  </bpmn:process>")

    lines.append("  <bpmndi:BPMNDiagram id=" + '"BPMNDiagram_1">')
    lines.append('    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="' +
                 _xml_escape(process_id) + '">')
    x = 160
    y = 120
    for idx, eid in enumerate(element_ids):
        lines.append(_render_diagram_shape(eid, x=x + idx * 140, y=y))
    for flow in flows:
        fid = _as_str(flow.get("id"), field="flows[].id")
        lines.append(_render_diagram_edge(fid))
    lines.append("    </bpmndi:BPMNPlane>")
    lines.append("  </bpmndi:BPMNDiagram>")
    lines.append("</bpmn:definitions>")
    return "\n".join(lines) + "\n", "process.bpmn"


def _render_bpmn_pool_lane(params: dict[str, Any]) -> tuple[str, str]:
    definitions_id = _as_str(params.get("definitions_id"), field="definitions_id")
    _check_identifier(definitions_id, field="definitions_id")
    process_id = _as_str(params.get("process_id"), field="process_id")
    _check_identifier(process_id, field="process_id")
    process_name = params.get("process_name") or ""
    elements_raw = _as_list(params.get("elements"), field="elements")
    if not elements_raw:
        raise NotationRenderError("elements must be a non-empty list")
    elements = _as_dict_entries(elements_raw, field="elements")
    flows_raw = _as_list(params.get("flows"), field="flows")
    flows = _as_dict_entries(flows_raw, field="flows")
    lanes_raw = _as_list(params.get("lanes"), field="lanes")
    if not lanes_raw:
        raise NotationRenderError("lanes must be a non-empty list")
    lanes = _as_dict_entries(lanes_raw, field="lanes")

    element_ids = _validate_flow_elements(elements)
    _validate_flows(flows, set(element_ids))

    seen_lane_ids: set[str] = set()
    lane_refs: list[set[str]] = []
    for lane in lanes:
        lid = _as_str(lane.get("id"), field="lanes[].id")
        _check_identifier(lid, field="lanes[].id")
        if lid in seen_lane_ids:
            raise NotationRenderError(f"duplicate lane id {lid!r}")
        seen_lane_ids.add(lid)
        refs = _as_list(lane.get("flow_node_refs", []), field="lanes[].flow_node_refs")
        ref_set: set[str] = set()
        for ref in refs:
            if not isinstance(ref, str):
                raise NotationRenderError(
                    f"lane {lid!r} flow_node_refs entries must be strings"
                )
            ref_set.add(ref)
        lane_refs.append(ref_set)

    element_set = set(element_ids)
    union: set[str] = set()
    for ref_set in lane_refs:
        for ref in ref_set:
            if ref not in element_set:
                raise NotationRenderError(
                    f"lane flow_node_ref {ref!r} references unknown element"
                )
            if ref in union:
                raise NotationRenderError(
                    f"element {ref!r} assigned to multiple lanes"
                )
            union.add(ref)
    if union != element_set:
        missing = element_set - union
        raise NotationRenderError(
            f"elements not assigned to any lane: {sorted(missing)}"
        )

    lines: list[str] = [_definitions_open(definitions_id)]
    proc_open = f'  <bpmn:process id="{_xml_escape(process_id)}" isExecutable="true"'
    if isinstance(process_name, str) and process_name:
        proc_open += f' name="{_xml_escape(process_name)}"'
    proc_open += ">"
    lines.append(proc_open)
    lines.append("    <bpmn:laneSet id=\"LaneSet_1\">")
    for lane in lanes:
        lid = _as_str(lane.get("id"), field="lanes[].id")
        lname = _as_str(lane.get("name", lid), field="lanes[].name")
        lines.append(f'      <bpmn:lane id="{_xml_escape(lid)}" '
                     f'name="{_xml_escape(lname)}">')
        for ref in _as_list(lane.get("flow_node_refs", []),
                            field="lanes[].flow_node_refs"):
            lines.append(f'        <bpmn:flowNodeRef>{_xml_escape(ref)}'
                         f'</bpmn:flowNodeRef>')
        lines.append("      </bpmn:lane>")
    lines.append("    </bpmn:laneSet>")
    for elem in elements:
        lines.append(_render_bpmn_element(elem))
    for flow in flows:
        lines.append(_render_bpmn_flow(flow))
    lines.append("  </bpmn:process>")

    lines.append('  <bpmndi:BPMNDiagram id="BPMNDiagram_1">')
    lines.append('    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="' +
                 _xml_escape(process_id) + '">')
    y_base = 120
    for li, lane in enumerate(lanes):
        lid = _as_str(lane.get("id"), field="lanes[].id")
        lname = _as_str(lane.get("name", lid), field="lanes[].name")
        lines.append(
            f'      <bpmndi:BPMNShape id="{_xml_escape(lid)}_di" '
            f'bpmnElement="{_xml_escape(lid)}" isHorizontal="true">\n'
            f'        <dc:Bounds x="120" y="{y_base + li * 140}" '
            f'width="900" height="120" />\n'
            f'      </bpmndi:BPMNShape>'
        )
        for idx, ref in enumerate(_as_list(lane.get("flow_node_refs", []),
                                           field="lanes[].flow_node_refs")):
            lines.append(_render_diagram_shape(
                ref, x=160 + idx * 140, y=y_base + li * 140 + 20
            ))
    for flow in flows:
        fid = _as_str(flow.get("id"), field="flows[].id")
        lines.append(_render_diagram_edge(fid))
    lines.append("    </bpmndi:BPMNPlane>")
    lines.append("  </bpmndi:BPMNDiagram>")
    lines.append("</bpmn:definitions>")
    return "\n".join(lines) + "\n", "process.bpmn"


def _render_bpmn_collaboration(params: dict[str, Any]) -> tuple[str, str]:
    definitions_id = _as_str(params.get("definitions_id"), field="definitions_id")
    _check_identifier(definitions_id, field="definitions_id")
    participants_raw = _as_list(params.get("participants"), field="participants")
    if not participants_raw:
        raise NotationRenderError("participants must be a non-empty list")
    participants = _as_dict_entries(participants_raw, field="participants")
    message_flows_raw = _as_list(params.get("message_flows"), field="message_flows")
    message_flows = _as_dict_entries(message_flows_raw, field="message_flows")

    seen_part_ids: set[str] = set()
    seen_proc_ids: set[str] = set()
    participant_data: list[dict] = []
    for p in participants:
        pid = _as_str(p.get("id"), field="participants[].id")
        _check_identifier(pid, field="participants[].id")
        if pid in seen_part_ids:
            raise NotationRenderError(f"duplicate participant id {pid!r}")
        seen_part_ids.add(pid)
        proc_id = _as_str(p.get("process_id"), field="participants[].process_id")
        _check_identifier(proc_id, field="participants[].process_id")
        if proc_id in seen_proc_ids:
            raise NotationRenderError(
                f"duplicate process_id {proc_id!r} across participants"
            )
        seen_proc_ids.add(proc_id)
        participant_data.append(p)

    seen_mf_ids: set[str] = set()
    for mf in message_flows:
        mid = _as_str(mf.get("id"), field="message_flows[].id")
        _check_identifier(mid, field="message_flows[].id")
        if mid in seen_mf_ids:
            raise NotationRenderError(f"duplicate messageFlow id {mid!r}")
        seen_mf_ids.add(mid)
        src = _as_str(mf.get("source_ref"), field="message_flows[].source_ref")
        tgt = _as_str(mf.get("target_ref"), field="message_flows[].target_ref")
        if src not in seen_part_ids:
            raise NotationRenderError(
                f"messageFlow {mid!r} source_ref {src!r} references "
                f"unknown participant"
            )
        if tgt not in seen_part_ids:
            raise NotationRenderError(
                f"messageFlow {mid!r} target_ref {tgt!r} references "
                f"unknown participant"
            )

    lines: list[str] = [_definitions_open(definitions_id)]

    for p in participant_data:
        pid = _as_str(p.get("id"), field="participants[].id")
        pname = _as_str(p.get("name", pid), field="participants[].name")
        proc_id = _as_str(p.get("process_id"), field="participants[].process_id")
        proc_name = p.get("process_name") or ""
        elements_raw = _as_list(p.get("elements", []),
                                field="participants[].elements")
        if not elements_raw:
            raise NotationRenderError(
                f"participant {pid!r} must carry at least one element"
            )
        elements = _as_dict_entries(elements_raw,
                                    field="participants[].elements")
        flows_raw = _as_list(p.get("flows", []),
                             field="participants[].flows")
        flows = _as_dict_entries(flows_raw, field="participants[].flows")
        element_ids = _validate_flow_elements(elements)
        _validate_flows(flows, set(element_ids))

        proc_open = (
            f'  <bpmn:process id="{_xml_escape(proc_id)}" isExecutable="true"'
        )
        if isinstance(proc_name, str) and proc_name:
            proc_open += f' name="{_xml_escape(proc_name)}"'
        proc_open += ">"
        lines.append(proc_open)

        lanes_raw = _as_list(p.get("lanes", []),
                             field="participants[].lanes")
        if lanes_raw:
            lanes = _as_dict_entries(lanes_raw,
                                     field="participants[].lanes")
            seen_lane_ids: set[str] = set()
            lane_refs: list[set[str]] = []
            element_set = set(element_ids)
            for lane in lanes:
                lid = _as_str(lane.get("id"), field="participants[].lanes[].id")
                _check_identifier(lid,
                                  field="participants[].lanes[].id")
                if lid in seen_lane_ids:
                    raise NotationRenderError(
                        f"duplicate lane id {lid!r} in participant {pid!r}"
                    )
                seen_lane_ids.add(lid)
                refs = _as_list(lane.get("flow_node_refs", []),
                                field="participants[].lanes[].flow_node_refs")
                ref_set: set[str] = set()
                for ref in refs:
                    if not isinstance(ref, str):
                        raise NotationRenderError(
                            f"lane {lid!r} flow_node_refs entries must be strings"
                        )
                    ref_set.add(ref)
                lane_refs.append(ref_set)
            union: set[str] = set()
            for ref_set in lane_refs:
                for ref in ref_set:
                    if ref not in element_set:
                        raise NotationRenderError(
                            f"lane flow_node_ref {ref!r} references unknown "
                            f"element in participant {pid!r}"
                        )
                    if ref in union:
                        raise NotationRenderError(
                            f"element {ref!r} assigned to multiple lanes in "
                            f"participant {pid!r}"
                        )
                    union.add(ref)
            if union != element_set:
                missing = element_set - union
                raise NotationRenderError(
                    f"elements not assigned to any lane in participant "
                    f"{pid!r}: {sorted(missing)}"
                )
            lines.append('    <bpmn:laneSet id="LaneSet_' + _xml_escape(proc_id) + '">')
            for lane in lanes:
                lid = _as_str(lane.get("id"),
                              field="participants[].lanes[].id")
                lname = _as_str(lane.get("name", lid),
                                field="participants[].lanes[].name")
                lines.append(
                    f'      <bpmn:lane id="{_xml_escape(lid)}" '
                    f'name="{_xml_escape(lname)}">'
                )
                for ref in _as_list(lane.get("flow_node_refs", []),
                                    field="participants[].lanes[].flow_node_refs"):
                    lines.append(
                        f'        <bpmn:flowNodeRef>{_xml_escape(ref)}'
                        f'</bpmn:flowNodeRef>'
                    )
                lines.append("      </bpmn:lane>")
            lines.append("    </bpmn:laneSet>")

        for elem in elements:
            lines.append(_render_bpmn_element(elem))
        for flow in flows:
            lines.append(_render_bpmn_flow(flow))
        lines.append("  </bpmn:process>")

    lines.append('  <bpmn:collaboration id="Collaboration_1">')
    for p in participant_data:
        pid = _as_str(p.get("id"), field="participants[].id")
        pname = _as_str(p.get("name", pid), field="participants[].name")
        proc_id = _as_str(p.get("process_id"),
                          field="participants[].process_id")
        lines.append(
            f'    <bpmn:participant id="{_xml_escape(pid)}" '
            f'name="{_xml_escape(pname)}" processRef="{_xml_escape(proc_id)}" />'
        )
    for mf in message_flows:
        mid = _as_str(mf.get("id"), field="message_flows[].id")
        src = _as_str(mf.get("source_ref"), field="message_flows[].source_ref")
        tgt = _as_str(mf.get("target_ref"), field="message_flows[].target_ref")
        name = mf.get("name")
        attrs = (
            f' id="{_xml_escape(mid)}" '
            f'sourceRef="{_xml_escape(src)}" '
            f'targetRef="{_xml_escape(tgt)}"'
        )
        if isinstance(name, str) and name:
            attrs += f' name="{_xml_escape(name)}"'
        lines.append(f"    <bpmn:messageFlow{attrs} />")
    lines.append("  </bpmn:collaboration>")

    lines.append('  <bpmndi:BPMNDiagram id="BPMNDiagram_1">')
    lines.append('    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Collaboration_1">')
    y_pool = 120
    for pi, p in enumerate(participant_data):
        pid = _as_str(p.get("id"), field="participants[].id")
        pname = _as_str(p.get("name", pid), field="participants[].name")
        proc_id = _as_str(p.get("process_id"),
                          field="participants[].process_id")
        lines.append(
            f'      <bpmndi:BPMNShape id="{_xml_escape(pid)}_di" '
            f'bpmnElement="{_xml_escape(pid)}" isHorizontal="true">\n'
            f'        <dc:Bounds x="120" y="{y_pool + pi * 220}" '
            f'width="900" height="200" />\n'
            f'      </bpmndi:BPMNShape>'
        )
        lines.append(
            f'      <bpmndi:BPMNShape id="{_xml_escape(proc_id)}_plane" '
            f'bpmnElement="{_xml_escape(proc_id)}" isHorizontal="true">\n'
            f'        <dc:Bounds x="160" y="{y_pool + pi * 220 + 20}" '
            f'width="840" height="160" />\n'
            f'      </bpmndi:BPMNShape>'
        )
        elements_raw = _as_list(p.get("elements", []),
                                field="participants[].elements")
        for idx, raw_elem in enumerate(elements_raw):
            elem = _as_dict_entries([raw_elem],
                                    field="participants[].elements")[0]
            eid = _as_str(elem.get("id"), field="participants[].elements[].id")
            lines.append(_render_diagram_shape(
                eid,
                x=200 + idx * 140,
                y=y_pool + pi * 220 + 60,
            ))
        flows_raw = _as_list(p.get("flows", []),
                             field="participants[].flows")
        for raw_flow in flows_raw:
            flow = _as_dict_entries([raw_flow],
                                    field="participants[].flows")[0]
            fid = _as_str(flow.get("id"), field="participants[].flows[].id")
            lines.append(_render_diagram_edge(fid))
    for mf in message_flows:
        mid = _as_str(mf.get("id"), field="message_flows[].id")
        lines.append(
            f'      <bpmndi:BPMNEdge id="{_xml_escape(mid)}_di" '
            f'bpmnElement="{_xml_escape(mid)}">\n'
            f'        <di:waypoint x="1020" y="220" />\n'
            f'        <di:waypoint x="1020" y="440" />\n'
            f'      </bpmndi:BPMNEdge>'
        )
    lines.append("    </bpmndi:BPMNPlane>")
    lines.append("  </bpmndi:BPMNDiagram>")
    lines.append("</bpmn:definitions>")
    return "\n".join(lines) + "\n", "collaboration.bpmn"
