"""Evaluation helpers for Native graph nodes: routing, conditions, inputs, artifacts, budgets, and result bindings."""

from __future__ import annotations

import math
from typing import Any

from agent.services.bpmn_input_projection import PROJECTION_KEY, project_bpmn_inputs
from agent.services.native_graph_models import NativeGraphRequest, NativeRunState
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeResult


class NativeGraphNodeEvaluationMixin:
    """Evaluate node routing, inputs, and results; mixed into ``NativeGraphOrchestrator``."""

    def _route_matches(self, plan: ExecutionPlan, node: ExecutionNode, state: NativeRunState) -> bool:
        edges = [edge for edge in plan.edges if edge.target == node.node_id]
        if not edges:
            return True
        results = [self._edge_result(edge, state) for edge in edges]
        if any(result.value is None for result in results):
            return False
        return (
            all(result.matches for result in results)
            if node.metadata.get("join_mode") == "all"
            else any(result.matches for result in results)
        )

    def _edge_result(self, edge, state):
        from agent.services.workflow_runtime.condition_evaluator import ConditionResult

        if edge.source in state.skipped:
            return ConditionResult(False, "native_source_skipped")
        return self._conditions.evaluate(edge.condition, self._condition_context(state))

    @staticmethod
    def _condition_context(state: NativeRunState, *, node: ExecutionNode | None = None) -> dict[str, Any]:
        if node is not None and PROJECTION_KEY in node.metadata:
            projected = project_bpmn_inputs(
                node.metadata[PROJECTION_KEY], input_data=state.input_data, results=state.node_results
            )
            return {"input": projected["workflow_input"], "results": projected["dependency_results"], "artifacts": {}}
        return {
            "input": dict(state.input_data),
            "results": dict(state.node_results),
            "artifacts": dict(state.artifact_refs),
            "status": state.status,
        }

    @staticmethod
    def _node_input(node: ExecutionNode, state: NativeRunState) -> dict[str, Any]:
        if PROJECTION_KEY in node.metadata:
            projected = project_bpmn_inputs(
                node.metadata[PROJECTION_KEY], input_data=state.input_data, results=state.node_results
            )
            return {**projected, "requested_artifacts": list(node.input_artifacts)}
        return {
            "workflow_input": dict(state.input_data),
            "dependency_results": {key: state.node_results[key] for key in sorted(state.node_results)},
            "requested_artifacts": list(node.input_artifacts),
        }

    @staticmethod
    def _validate_artifacts(node: ExecutionNode, result: NativeNodeResult) -> None:
        unexpected = set(result.artifact_refs) - set(node.output_artifacts)
        missing = set(node.output_artifacts) - set(result.artifact_refs)
        if unexpected:
            raise ValueError("native_node_artifact_undeclared")
        if missing:
            raise ValueError("native_node_artifact_missing")

    def _consume_budget(self, plan: ExecutionPlan, state: NativeRunState, result: NativeNodeResult) -> None:
        for key, value in result.budget_usage.items():
            state.budget_usage[key] = state.budget_usage.get(key, 0) + value

    @staticmethod
    def _budget_exceeded(plan: ExecutionPlan, state: NativeRunState, result: NativeNodeResult) -> str:
        combined = dict(state.budget_usage)
        for key, value in result.budget_usage.items():
            combined[key] = combined.get(key, 0) + value
            if not math.isfinite(combined[key]):
                return "native_budget_exceeded:" + key
        limits = {
            "tokens": plan.budget.max_tokens,
            "cost_micros": plan.budget.max_cost_micros,
        }
        for key, limit in limits.items():
            if limit is not None and combined.get(key, 0) > limit:
                return f"native_budget_exceeded:{key}"
        return ""

    @staticmethod
    def _assert_result_binding(
        plan: ExecutionPlan,
        request: NativeGraphRequest,
        result: NativeNodeResult,
        running: dict[str, Any],
    ) -> None:
        result.assert_valid()
        expected = {
            "tenant_id": plan.tenant_id,
            "workflow_id": plan.workflow_id,
            "run_id": request.run_id,
            "command_id": running["command_id"],
            "hub_task_id": running["hub_task_id"],
            "attempt_id": running["attempt_id"],
            "fencing_token": running["fencing_token"],
        }
        if any(getattr(result, key) != value for key, value in expected.items()):
            raise ValueError("native_node_result_binding_mismatch")
