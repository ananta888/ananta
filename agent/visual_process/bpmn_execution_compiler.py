"""Lossless BPMN control projection; contracts are data, never executable code."""

from __future__ import annotations

import re
from copy import deepcopy

from agent.services.bpmn_input_projection import (
    PROJECTION_KEY,
    projection_result_dependencies,
    validate_input_projection,
)
from agent.visual_process.bpmn_conditions import compile_condition
from agent.visual_process.bpmn_execution_support import (
    SOURCE_KEY,
    BpmnExecutionError,
    BpmnExecutionIssue,
    assert_bpmn_source_supported,
    has_bpmn_source_metadata,
)
from agent.visual_process.bpmn_xml_region_contracts import BOUND_METADATA, CONTROL_KEY, JOIN_KEY, REGIONS_KEY

SCHEMA = "ananta.bpmn_execution_graph.v1"
_IDENTITY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_CONTROL = {"start": "start", "end": "end", "decision": "exclusive", "parallel": "parallel"}


def compile_bpmn_graph(graph):
    """Return a closed graph projection for BPMN, or None for a legacy graph."""
    if not has_bpmn_source_metadata(graph.metadata) and not any("bpmn_element_type" in s.metadata for s in graph.steps):
        if any(edge.condition.kind != "always" for edge in graph.edges):
            _reject(
                graph.id,
                "bpmn_lossy_legacy_conditions",
                "Explicit execution semantics are required for conditional graph edges.",
            )
        return None
    assert_bpmn_source_supported(graph)
    from agent.visual_process.bpmn_adapter import import_bpmn_xml

    try:
        admitted = import_bpmn_xml(graph.metadata[SOURCE_KEY]).graph
    except (ValueError, TypeError, RecursionError):
        _reject(graph.id, "bpmn_source_invalid", "The bound source cannot be converted safely.")
    # Positions and labels may be edited. Expanded regions also bind every
    # child's task semantics to the source; edits require re-importing XML.
    if _shape(graph) != _shape(admitted):
        _reject(graph.id, "bpmn_source_binding_mismatch", "Re-export and import the changed BPMN definition.")
    if graph.has_cycles() or any(edge.is_back_edge() for edge in graph.edges):
        _reject(graph.id, "bpmn_loop_unsupported", "Cyclic activation is not yet executable.")
    ids = [step.id for step in graph.steps] + [edge.id for edge in graph.edges]
    if len(set(ids)) != len(ids) or any(not _IDENTITY.fullmatch(value) for value in ids):
        _reject(
            graph.id,
            "bpmn_execution_id_invalid",
            "Use unique letters, digits, underscores and hyphens; start with a letter or underscore.",
        )
    controls = {}
    waits = {}
    node_metadata = {}
    edges = []
    for step in graph.steps:
        original = admitted.step_by_id(step.id)
        _validate_node_binding(step, original)
        element_type = original.metadata["bpmn_element_type"]
        outgoing = graph.edges_from(step.id)
        incoming = graph.edges_to(step.id)
        if len({edge.target for edge in outgoing}) != len(outgoing):
            _reject(
                step.id,
                "bpmn_duplicate_edge_target_unsupported",
                "Multiple flows between the same nodes require a dedicated token contract.",
            )
        control = _CONTROL.get(step.kind)
        if original.metadata.get(CONTROL_KEY) == "projection":
            control = "projection"
        if element_type == "intermediateCatchEvent":
            waits[step.id] = deepcopy(original.metadata["bpmn_wait"])
        bound = {key: deepcopy(original.metadata[key]) for key in BOUND_METADATA if key in original.metadata}
        if bound:
            node_metadata[step.id] = bound
        _validate_projection(graph, step)
        if control == "start" and incoming or control == "end" and outgoing:
            _reject(step.id, "bpmn_event_topology_invalid", "Start has no incoming flow; end has no outgoing flow.")
        if len(incoming) > 1 and control not in {"exclusive", "parallel"} and not original.metadata.get(JOIN_KEY):
            _reject(step.id, "bpmn_implicit_merge_unsupported", "Model convergence explicitly with a gateway.")
        if control != "end" and not outgoing:
            _reject(step.id, "bpmn_dangling_node", "Only an end event may terminate a path.")
        if control != "start" and not incoming:
            _reject(step.id, "bpmn_dangling_node", "Only a plain start event may activate a path.")
        if control:
            controls[step.id] = {"kind": control, "outgoing": []}
            if step.io.inputs or step.io.outputs or step.metadata.get("allowed_tools"):
                _reject(
                    step.id,
                    "bpmn_control_worker_effects_forbidden",
                    "Control nodes route; worker tasks own tools and artifacts.",
                )
        if step.id in waits and (step.gate or step.io.inputs or step.io.outputs or step.metadata.get("allowed_tools")):
            _reject(step.id, "bpmn_wait_worker_effects_forbidden", "Waits are Hub-only durable control nodes.")
        if step.kind == "human_task" and not step.gate:
            _reject(step.id, "bpmn_user_gate_required", "User tasks require the Hub approval contract.")
        if control in {"exclusive", "parallel"} and len(incoming) > 1 and len(outgoing) > 1:
            _reject(step.id, "bpmn_mixed_gateway_unsupported", "Separate converging and diverging gateways.")
        default = step.metadata.get("bpmn_default_flow", "")
        if default and default not in {edge.id for edge in outgoing}:
            _reject(step.id, "bpmn_default_flow_invalid", "Default must reference an outgoing edge.")
        for edge in outgoing:
            condition = {"op": "always"}
            if edge.condition.kind == "expression":
                condition = compile_condition(edge.condition.expression, element_id=edge.id)
                _validate_snapshot_fields(graph, step.id, edge.id, condition)
            elif edge.condition.kind != "always":
                _reject(edge.id, "bpmn_condition_kind_unsupported", edge.condition.kind)
            if control != "exclusive" and edge.condition.kind != "always":
                _reject(edge.id, "bpmn_conditional_flow_requires_xor", "Use an exclusive gateway.")
            if control == "exclusive":
                if edge.id == default and edge.condition.kind != "always":
                    _reject(edge.id, "bpmn_default_condition_forbidden", "Default edges must not contain a condition.")
                controls[step.id]["outgoing"].append(
                    {
                        "id": edge.id,
                        "target": edge.target,
                        "condition": condition,
                        "default": edge.id == default,
                    }
                )
                condition = {"op": "eq", "field": f"results.{step.id}.selected_edge", "value": edge.id}
            edges.append({"id": edge.id, "source": edge.source, "target": edge.target, "condition": condition})
    return {
        "schema": SCHEMA,
        "controls": controls,
        "edges": edges,
        "definition_hash": admitted.definition_hash(),
        **({"node_metadata": node_metadata} if node_metadata else {}),
        **({"waits": waits} if waits else {}),
    }


def _validate_node_binding(step, original):
    if original.gate and not step.gate:
        _reject(
            step.id, "bpmn_gate_binding_mismatch", "An XML-declared gate cannot be removed by editing a projection."
        )
    required_kind = {
        "startEvent": "start",
        "endEvent": "end",
        "exclusiveGateway": "decision",
        "parallelGateway": "parallel",
        "userTask": "human_task",
        "intermediateCatchEvent": "bpmn_wait",
    }.get(original.metadata["bpmn_element_type"])
    if required_kind and step.kind != required_kind:
        _reject(step.id, "bpmn_kind_binding_mismatch", "Metadata cannot replace BPMN control or user-task semantics.")
    if not required_kind and step.kind in {*_CONTROL, "bpmn_wait"}:
        _reject(step.id, "bpmn_kind_binding_mismatch", "Worker tasks cannot become control nodes through metadata.")
    if (step.kind in _CONTROL or original.kind in _CONTROL) and step.kind != original.kind:
        _reject(step.id, "bpmn_control_binding_mismatch", "Control nodes cannot become worker tasks or vice versa.")


def _shape(graph):
    return (
        [
            (
                step.id,
                step.metadata.get("bpmn_element_type"),
                step.metadata.get("bpmn_default_flow", ""),
                {key: step.metadata[key] for key in (*BOUND_METADATA, "bpmn_wait") if key in step.metadata},
                (
                    step.kind,
                    step.role,
                    step.agent_skill_profile_id,
                    step.io.model_dump(),
                    step.policy_hints,
                    step.gate,
                    step.metadata,
                )
                if graph.metadata.get(REGIONS_KEY)
                else None,
            )
            for step in graph.steps
        ],
        [
            (
                edge.id,
                edge.source,
                edge.target,
                edge.condition.model_dump(),
                edge.metadata.get("bpmn_activation_origin"),
            )
            for edge in graph.edges
        ],
        graph.metadata.get(REGIONS_KEY),
        graph.metadata.get("bpmn_source_sha256"),
    )


def _validate_snapshot_fields(graph, node_id, edge_id, condition):
    projection = graph.step_by_id(node_id).metadata.get(PROJECTION_KEY)
    if projection is not None:
        pending = [condition]
        while pending:
            item = pending.pop()
            pending.extend(item.get("conditions", []))
            if "condition" in item:
                pending.append(item["condition"])
            if "field" in item:
                parts = item["field"].split(".")
                group = {"input": "workflow_input", "results": "dependency_results"}.get(parts[0])
                if group is None or parts[1] not in projection[group]:
                    _reject(
                        edge_id, "bpmn_condition_projection_unbound", "Only explicit projected aliases are visible."
                    )
        return
    ancestors = _ancestors(graph, node_id)
    available_artifacts = {name for step in graph.steps if step.id in ancestors for name in step.io.output_names()}
    predicates = [condition]
    while predicates:
        predicate = predicates.pop()
        predicates.extend(predicate.get("conditions", []))
        if "condition" in predicate:
            predicates.append(predicate["condition"])
        segments = predicate.get("field", "").split(".")
        if segments[0] == "results" and segments[1] not in ancestors:
            _reject(
                edge_id, "bpmn_unbound_result_reference", "Only predecessor results belong to this gateway snapshot."
            )
        if segments[0] == "artifacts" and segments[1] not in available_artifacts:
            _reject(edge_id, "bpmn_unbound_artifact_reference", "Artifacts must be produced by a predecessor.")


def _ancestors(graph, node_id):
    incoming = {}
    for edge in graph.edges:
        incoming.setdefault(edge.target, []).append(edge.source)
    ancestors = set()
    pending = list(incoming.get(node_id, ()))
    while pending:
        predecessor = pending.pop()
        if predecessor in ancestors:
            continue
        ancestors.add(predecessor)
        pending.extend(incoming.get(predecessor, ()))
    return ancestors


def _validate_projection(graph, step):
    value = step.metadata.get(PROJECTION_KEY)
    if value is None:
        return
    errors = validate_input_projection(value)
    if errors:
        _reject(step.id, errors[0], "Invalid scoped input contract.")
    if projection_result_dependencies(value) - _ancestors(graph, step.id):
        _reject(step.id, "bpmn_projection_result_not_predecessor", "Projection sources must precede the consumer.")


def _reject(element_id, reason, detail):
    raise BpmnExecutionError([BpmnExecutionIssue(reason, element_id, detail)])
