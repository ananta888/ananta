"""VisualProcess → Blueprint/Workflow mapper (VPAD-003).

Converts a VisualProcessGraph into the dict structure expected by
BlueprintWorkflowStepDB / the blueprint catalog JSON.

Output schema mirrors BlueprintWorkflowStepDB fields:
  step_id, role_name, task_kind, title, description,
  produces, consumes, depends_on, gate, sort_order
"""
from __future__ import annotations

from typing import Any

from agent.visual_process.models import VisualProcessGraph, VisualProcessStep
from agent.services.workflow_backend import WorkflowRequest, WorkflowStepRequest


def graph_to_blueprint_steps(graph: VisualProcessGraph) -> list[dict[str, Any]]:
    """Return a list of workflow step dicts compatible with BlueprintWorkflowStepDB."""
    order = _topological_order(graph)
    steps: list[dict[str, Any]] = []
    for sort_idx, step in enumerate(order):
        predecessors = [e.source for e in graph.edges_to(step.id) if not e.is_back_edge()]
        steps.append({
            "step_id": step.id,
            "role_name": step.role or "default",
            "task_kind": step.kind,
            "title": step.label,
            "description": step.metadata.get("description") or "",
            "produces": step.io.output_names(),
            "consumes": step.io.input_names(),
            "depends_on": predecessors,
            "gate": step.gate,
            "sort_order": sort_idx,
            "checks": _build_checks(step),
        })
    return steps


def graph_to_blueprint_dict(graph: VisualProcessGraph) -> dict[str, Any]:
    """Return a full blueprint dict (catalog-compatible JSON structure)."""
    return {
        "id": graph.id,
        "name": graph.name,
        "description": graph.description,
        "version": graph.version,
        "tags": graph.tags,
        "workflow": {
            "steps": graph_to_blueprint_steps(graph),
        },
        "metadata": graph.metadata,
    }


def graph_to_workflow_execution_request(
    graph: VisualProcessGraph,
    *,
    provider: str = "ananta",
    requested_by: str = "visual_process_designer",
    dry_run: bool = True,
    input_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a WorkflowExecutionRequestModel-compatible dict."""
    return {
        "provider": provider,
        "workflow_id": graph.id,
        "input_payload": input_payload or {},
        "dry_run": dry_run,
        "requested_by": requested_by,
    }


def graph_to_workflow_request(
    graph: VisualProcessGraph,
    *,
    goal_id: str = "",
    plan_id: str = "",
    blueprint_id: str | None = None,
    blueprint_version: str | None = None,
    workflow_type: str = "custom",
    policy_scope: dict[str, Any] | None = None,
    allowed_tools: list[str] | None = None,
    requested_by: str = "visual_process_designer",
) -> WorkflowRequest:
    """Compile a VisualProcessGraph into the neutral WorkflowBackend request."""
    steps = []
    for step in _topological_order(graph):
        predecessors = [e.source for e in graph.edges_to(step.id) if not e.is_back_edge()]
        step_scope = dict(policy_scope or {})
        step_scope.update(dict(step.metadata.get("policy_scope") or step.metadata.get("policyScope") or {}))
        steps.append(WorkflowStepRequest(
            step_id=step.id,
            title=step.label,
            task_kind=step.kind,
            role=step.role or step.agent_skill_profile_id or "default",
            depends_on=tuple(predecessors),
            gate=step.gate,
            allowed_tools=tuple(str(v) for v in list(step.metadata.get("allowed_tools") or allowed_tools or [])),
            policy_scope=step_scope,
            input_artifacts=tuple(step.io.input_names()),
            output_artifacts=tuple(step.io.output_names()),
            metadata={
                **dict(step.metadata or {}),
                "agent_skill_profile_id": step.agent_skill_profile_id,
                "policy_hints": list(step.policy_hints),
            },
        ))
    return WorkflowRequest(
        workflow_id=graph.id,
        workflow_type=workflow_type,
        goal_id=goal_id,
        plan_id=plan_id,
        blueprint_id=blueprint_id or graph.id,
        blueprint_version=blueprint_version or graph.version,
        steps=tuple(steps),
        allowed_tools=tuple(str(v) for v in list(allowed_tools or [])),
        policy_scope=dict(policy_scope or {}),
        requested_by=requested_by,
        metadata={"source": "visual_process_graph", **dict(graph.metadata or {})},
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _topological_order(graph: VisualProcessGraph) -> list[VisualProcessStep]:
    """Kahn's algorithm on forward edges."""
    from collections import deque
    forward = [(e.source, e.target) for e in graph.edges if not e.is_back_edge()]
    in_degree: dict[str, int] = {s.id: 0 for s in graph.steps}
    for _, tgt in forward:
        in_degree[tgt] = in_degree.get(tgt, 0) + 1
    queue: deque[str] = deque(sid for sid, deg in in_degree.items() if deg == 0)
    result: list[VisualProcessStep] = []
    step_index = {s.id: s for s in graph.steps}
    while queue:
        sid = queue.popleft()
        if step := step_index.get(sid):
            result.append(step)
        for src, tgt in forward:
            if src == sid:
                in_degree[tgt] -= 1
                if in_degree[tgt] == 0:
                    queue.append(tgt)
    # Append any remaining (shouldn't happen for valid DAG)
    seen = {s.id for s in result}
    result += [s for s in graph.steps if s.id not in seen]
    return result


def _build_checks(step: VisualProcessStep) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    if step.gate:
        checks["approval_required"] = True
    if step.policy_hints:
        checks["policy_hints"] = step.policy_hints
    return checks
