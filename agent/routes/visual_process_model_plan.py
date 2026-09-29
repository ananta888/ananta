"""Workflow option parsing/compilation and the per-step model routing plan of a graph."""

from __future__ import annotations

import json
from typing import Any

from agent.services.workflow_backend import WorkflowRequest
from agent.visual_process.blueprint_mapper import graph_to_workflow_request
from agent.visual_process.models import VisualProcessGraph


def _workflow_options(body: dict) -> dict:
    return {
        "goal_id": body.get("goal_id") or body.get("goalId") or "",
        "plan_id": body.get("plan_id") or body.get("planId") or "",
        "blueprint_id": body.get("blueprint_id") or body.get("blueprintId"),
        "blueprint_version": body.get("blueprint_version") or body.get("blueprintVersion"),
        "workflow_type": body.get("workflow_type") or body.get("workflowType") or "visual_process",
        "policy_scope": body.get("policy_scope") or body.get("policyScope") or {"source": "visual_process"},
        "allowed_tools": body.get("allowed_tools") or body.get("allowedTools"),
        "requested_by": body.get("requested_by") or body.get("requestedBy") or "visual_process_designer",
    }


def _compile_workflow_request(graph: VisualProcessGraph, body: dict) -> WorkflowRequest:
    return graph_to_workflow_request(graph, **_workflow_options(body))


def _effective_step_routing(graph: VisualProcessGraph, step) -> dict:
    from agent.visual_process.models import ModelRoutingConfig

    merged: dict = {}
    graph_routing = ModelRoutingConfig.from_metadata(graph.metadata)
    step_routing = ModelRoutingConfig.from_metadata(step.metadata)
    if graph_routing is not None:
        merged.update(graph_routing.as_metadata())
    if step_routing is not None:
        merged.update(step_routing.as_metadata())
    return merged


def _build_model_plan(graph: VisualProcessGraph) -> dict:
    from agent.services.model_cost_estimator import ModelCostEstimator
    from agent.services.model_invocation_service import ModelInvocationService
    from agent.services.model_profile_resolver import RoutingContext

    try:
        resolver = ModelInvocationService._get_resolver()
    except Exception:
        resolver = None
    if resolver is None:
        return {
            "per_step_model_plan": [],
            "model_routing_summary": {"status": "not_configured", "total_estimated_cost": 0.0},
        }

    estimator = ModelCostEstimator()
    per_step: list[dict] = []
    total_cost = 0.0
    for step in graph.steps:
        routing = _effective_step_routing(graph, step)
        context_text = json.dumps({"graph": graph.metadata, "step": step.metadata}, sort_keys=True)
        ctx = RoutingContext(
            model_role=str(routing.get("model_role") or routing.get("default_model_role") or step.role or "any"),
            task_kind=step.kind,
            step_kind=step.kind,
            context_text=context_text,
            request_profile_id=routing.get("preferred_profile_id"),
            fallback_group_id=routing.get("fallback_group_id"),
            requires_json=bool(routing.get("requires_json", False)),
            requires_tools=bool(routing.get("requires_tools", False)),
            allow_cloud=bool(routing.get("allow_cloud", False)),
            max_estimated_cost_per_step=routing.get("max_estimated_cost"),
            metadata=routing,
        )
        result, chain = resolver.resolve_candidate_chain(ctx)
        selected = result.profile
        estimate = estimator.estimate_for_profile(selected, prompt_text=context_text).as_dict() if selected else None
        if estimate:
            total_cost += float(estimate["estimated_total_cost"])
        per_step.append(
            {
                "step_id": step.id,
                "model_role": ctx.model_role,
                "selected_profile_id": selected.profile_id if selected else None,
                "provider_id": selected.provider_id if selected else None,
                "model": selected.model if selected else None,
                "resolver_source": result.final_source,
                "resolver_rank": result.final_rank,
                "fallback_group_id": routing.get("fallback_group_id") or getattr(selected, "fallback_group", None),
                "context_recovery_strategies": list(routing.get("context_recovery_strategies") or []),
                "require_approval_for_generated_plan": bool(routing.get("require_approval_for_generated_plan", True)),
                "candidate_chain": [p.profile_id for p in chain],
                "cloud_allowed": bool(routing.get("allow_cloud", False)),
                "blocked_candidates": [
                    {"profile_id": pid, "reason": reason} for pid, reason in list(result.blocked_candidates or [])
                ],
                "policy_decisions": [
                    {
                        "rank": d.rank,
                        "source": d.source,
                        "profile_id": d.profile_id,
                        "accepted": d.accepted,
                        "reason": d.reason,
                    }
                    for d in list(result.decisions or [])
                ],
                "estimated_cost": estimate,
            }
        )
    return {
        "per_step_model_plan": per_step,
        "model_routing_summary": {
            "status": "ready",
            "step_count": len(per_step),
            "total_estimated_cost": round(total_cost, 8),
        },
    }


def _invalid_model_plan() -> dict[str, Any]:
    return {
        "per_step_model_plan": [],
        "model_routing_summary": {
            "status": "invalid",
            "total_estimated_cost": 0.0,
        },
    }
