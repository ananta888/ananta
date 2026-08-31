"""Deterministic query-rewrite visual-process adapter."""

from __future__ import annotations

from typing import Any

from agent.visual_process.models import VisualProcessStep
from agent.visual_process.step_executor import StepAdapter, StepExecutionResult


class QueryRewriteAdapter(StepAdapter):
    @property
    def kind(self) -> str:
        return "query_rewrite"

    def execute(
        self,
        step: VisualProcessStep,
        artifacts: dict[str, Any],
        context: dict[str, Any],
    ) -> StepExecutionResult:
        from worker.retrieval.query_rewrite import rewrite_query

        query = str(artifacts.get("query") or step.metadata.get("query") or "")
        return StepExecutionResult(
            status="success",
            outputs=rewrite_query(query),
            backend_service="rewrite_query",
            executable=True,
            execution_reason="vp_adapter: synonym expansion (deterministic, no LLM, no network)",
        )
