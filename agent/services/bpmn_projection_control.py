"""Pure Hub projection controls, including one active bounded-loop return.

Plan validation calls ``validate_projection_control(node, incoming_sources=...)``.
Native calls ``project_control_result`` only after normal DAG readiness/route
checks. This module does not schedule nodes, mutate state, or authorize work.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from agent.services.bpmn_input_projection import (
    PROJECTION_KEY,
    PROJECTION_SCHEMA,
    project_bpmn_inputs,
    validate_input_projection,
)

JOIN_KEY = "bpmn_projection_join"
JOIN_SCHEMA = "ananta.bpmn_projection_join.v1"
_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,127}$")


def validate_projection_control(node, *, incoming_sources: Iterable[str] | None = None) -> tuple[str, ...]:
    """Validate the closed join shape and, at plan level, its exact predecessors."""
    spec = node.metadata.get(JOIN_KEY)
    if spec is None:
        return ()
    control = node.metadata.get("bpmn_control", {})
    if node.node_type != "bpmn_control" or control != {"kind": "projection", "outgoing": []}:
        return ("bpmn_projection_join_control_required",)
    if not isinstance(spec, dict) or set(spec) != {"schema", "sources"} or spec["schema"] != JOIN_SCHEMA:
        return ("bpmn_projection_join_invalid",)
    sources = spec["sources"]
    if (
        not isinstance(sources, list)
        or not 1 <= len(sources) <= 257
        or any(not isinstance(value, str) or not _ID.fullmatch(value) for value in sources)
        or len(set(sources)) != len(sources)
        or node.node_id in sources
    ):
        return ("bpmn_projection_join_sources_invalid",)
    projection = node.metadata.get(PROJECTION_KEY)
    issues = validate_input_projection(projection)
    if issues:
        return issues
    if projection["workflow_input"] or projection["dependency_results"]:
        return ("bpmn_projection_join_empty_view_required",)
    if incoming_sources is not None and set(sources) != set(incoming_sources):
        return ("bpmn_projection_join_predecessors_mismatch",)
    return ()


def validate_region_budget(plan) -> tuple[str, ...]:
    """XML regions require a finite Hub cost ceiling, inherited by every child.

    No default authority is invented here. Admission supplies the configured plan
    budget; the shared ExecutionBudget validator checks attempts/time/token types.
    """
    if not any(
        isinstance(node.metadata.get("bpmn_activation_origin"), dict)
        and node.metadata["bpmn_activation_origin"].get("schema") == "ananta.bpmn_xml_origin.v1"
        for node in plan.nodes
    ):
        return ()
    if type(plan.budget.max_cost_micros) is not int or plan.budget.max_cost_micros < 0:
        return ("bpmn_region_finite_cost_budget_required",)
    for node in plan.nodes:
        if node.budget is None:
            continue
        for key in ("max_cost_micros", "timeout_seconds", "max_attempts", "max_tokens"):
            parent, child = getattr(plan.budget, key), getattr(node.budget, key)
            if parent is not None and (child is None or child > parent):
                return ("bpmn_region_budget_escalation",)
    return ()


def project_control_result(
    node,
    *,
    input_data: dict,
    results: dict,
    completed_node_ids: Iterable[str],
    skipped_node_ids: Iterable[str],
) -> dict:
    """Return a detached explicit projection or the sole active exported result.

    No order-based selection, union of results, or ambient-input fallback exists.
    Every join source must be terminal before selection. A completed source must
    have an object result, and completed/skipped state may not overlap.
    """
    issues = validate_projection_control(node)
    if issues:
        raise ValueError(issues[0])
    spec = node.metadata.get(JOIN_KEY)
    if spec is None:
        return project_bpmn_inputs(node.metadata[PROJECTION_KEY], input_data=input_data, results=results)[
            "workflow_input"
        ]
    sources = set(spec["sources"])
    completed, skipped = set(completed_node_ids) & sources, set(skipped_node_ids) & sources
    if completed & skipped:
        raise ValueError("bpmn_projection_join_state_conflict")
    if sources - completed - skipped:
        raise ValueError("bpmn_projection_join_sources_not_terminal")
    if len(completed) != 1:
        raise ValueError("bpmn_projection_join_single_source_required")
    source = next(iter(completed))
    if not isinstance(results.get(source), dict):
        raise ValueError("bpmn_projection_join_object_result_required")
    # Reuse the bounded dictionary-only projector for validation and detaching
    # the result. The constant alias never becomes a published output field.
    view = {
        "schema": PROJECTION_SCHEMA,
        "workflow_input": {"selected": ["results", source]},
        "dependency_results": {},
    }
    return project_bpmn_inputs(view, input_data={}, results=results)["workflow_input"]["selected"]
