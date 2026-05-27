"""SummaryRecomputeStrategy — planning_summary_recompute_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import ArtifactRefPort, HeuristicStrategyBase, TodoReadPort
from agent.heuristics.strategies.scoring import build_reason_codes, keyword_score
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_SUMMARY_KEYWORDS = [
    "summary", "summarize", "recompute", "update", "refresh", "overview",
    "status", "progress", "recap", "report",
]
_MIN_SCORE = 0.1


class SummaryRecomputeStrategy(HeuristicStrategyBase):
    """Suggest recomputing a goal/task summary when context changes.

    Activates when the query asks for an updated summary or status report.
    Surfaces active goal/task context from todo scopes. Deterministic.
    """

    def domain(self) -> str:
        return "planning"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        todo = TodoReadPort.from_context(context)
        art = ArtifactRefPort.from_context(context)
        params = definition.parameters or {}
        query = str(context.query or "")
        min_score = float(params.get("min_score", _MIN_SCORE))

        score = keyword_score(query, _SUMMARY_KEYWORDS)

        if score < min_score:
            return DecisionResult(
                action_kind="no_action",
                confidence=1.0,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["summary_recompute:no_summary_keywords"],
            )

        has_context = bool(todo.todo_scopes or art.active_goal_id or art.active_task_id)
        if not has_context:
            return DecisionResult(
                action_kind="ask_scope",
                confidence=0.6,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["summary_recompute:no_context"],
            )

        codes = build_reason_codes(
            f"summary_recompute:score={score:.2f}",
            f"goal:{art.active_goal_id or 'none'}",
            f"task:{art.active_task_id or 'none'}",
        )
        return DecisionResult(
            action_kind="show_context_summary",
            confidence=min(0.68 + score * 0.27, 0.9),
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=codes,
        )
