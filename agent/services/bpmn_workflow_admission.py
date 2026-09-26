"""Revalidate a BPMN request at the workflow-contract boundary.

Kept separate from the neutral request DTO and from HTTP transport. Compiled
projections and import warnings are client data, never admission authority.
"""

from __future__ import annotations


def validate_bpmn_request(request) -> list[str]:
    from agent.visual_process.bpmn_adapter import import_bpmn_xml
    from agent.visual_process.bpmn_execution_compiler import compile_bpmn_graph
    from agent.visual_process.bpmn_execution_support import SOURCE_KEY, BpmnExecutionError, has_bpmn_source_metadata
    from agent.visual_process.bpmn_xml_region_contracts import BOUND_METADATA

    metadata = getattr(request, "metadata", {}) or {}
    source = metadata.get(SOURCE_KEY)
    projection = getattr(request, "execution_graph", None)
    marked = (
        getattr(request, "workflow_type", "") == "bpmn"
        or has_bpmn_source_metadata(metadata)
        or any("bpmn_element_type" in (getattr(step, "metadata", {}) or {}) for step in request.steps)
    )
    if projection is None and not source and not marked:
        return []
    if not isinstance(source, str) or not source:
        return ["bpmn_source_required"]
    if not isinstance(projection, dict):
        return ["bpmn_execution_graph_required"]
    try:
        graph = import_bpmn_xml(source).graph
        expected = compile_bpmn_graph(graph)
    except BpmnExecutionError as exc:
        return [issue.reason_code for issue in exc.issues]
    except (ValueError, TypeError, KeyError, RecursionError):
        return ["bpmn_source_invalid"]
    for key in expected:
        if projection.get(key) != expected.get(key):
            return ["bpmn_execution_graph_mismatch"]
    if set(projection) != set(expected):
        return ["bpmn_execution_graph_fields_invalid"]
    if len(request.steps) != len(graph.steps) or {step.step_id for step in request.steps} != set(graph.step_ids()):
        return ["bpmn_step_binding_mismatch"]
    if graph.metadata.get("bpmn_xml_regions") and any(
        metadata.get(key) != graph.metadata.get(key) for key in ("bpmn_xml_regions", "bpmn_source_sha256")
    ):
        return ["bpmn_region_source_binding_mismatch"]
    control_kinds = {"start", "end", "decision", "parallel", "bpmn_wait"}
    errors = []
    for step in request.steps:
        original = graph.step_by_id(step.step_id)
        if graph.metadata.get("bpmn_xml_regions"):
            if step.task_kind != original.kind or step.role != (
                original.role or original.agent_skill_profile_id or "default"
            ):
                errors.append("bpmn_region_task_binding_mismatch")
            ceiling = set(original.metadata.get("allowed_tools") or request.allowed_tools)
            if set(step.allowed_tools) - ceiling or set(step.allowed_tools) - set(request.allowed_tools):
                errors.append("bpmn_region_tool_escalation")
            if step.policy_scope != request.policy_scope:
                errors.append("bpmn_region_policy_binding_mismatch")
            if tuple(step.input_artifacts) != tuple(original.io.input_names()) or tuple(step.output_artifacts) != tuple(
                original.io.output_names()
            ):
                errors.append("bpmn_region_artifact_binding_mismatch")
        for key in (*BOUND_METADATA, "bpmn_wait"):
            if (key in step.metadata) != (key in original.metadata) or step.metadata.get(key) != original.metadata.get(
                key
            ):
                errors.append("bpmn_step_metadata_binding_mismatch")
        if original.gate and not step.gate:
            errors.append("bpmn_gate_binding_mismatch")
        if set(step.depends_on) != {edge.source for edge in graph.edges_to(step.step_id)}:
            errors.append("bpmn_dependency_binding_mismatch")
        if (original.kind in control_kinds or step.task_kind in control_kinds) and step.task_kind != original.kind:
            errors.append("bpmn_control_binding_mismatch")
        if original.kind == "human_task" and not step.gate:
            errors.append("bpmn_user_gate_required")
    return sorted(set(errors))
