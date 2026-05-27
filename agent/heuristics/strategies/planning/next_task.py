"""NextTaskStrategy — planning_next_task_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import ArtifactRefPort, HeuristicStrategyBase, TodoReadPort
from agent.heuristics.strategies.scoring import build_reason_codes, keyword_score
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_NEXT_TASK_KEYWORDS = [
    "next", "what should", "what to do", "priorit", "pick", "start",
    "ready", "unblocked", "todo", "plan", "work on",
]
_MIN_SCORE = 0.1


class NextTaskStrategy(HeuristicStrategyBase):
    """Suggest the next task from planning todo scopes.

    Activates when the query asks what to work on next. Reads task state
    from todo source scopes. Never invents tasks — only surfaces existing refs.
    Deterministic.
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

        score = keyword_score(query, _NEXT_TASK_KEYWORDS)

        if score < min_score:
            return DecisionResult(
                action_kind="no_action",
                confidence=1.0,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["next_task:no_planning_keywords"],
            )

        if not todo.todo_scopes:
            return DecisionResult(
                action_kind="ask_scope",
                confidence=0.65,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["next_task:no_todo_scopes"],
            )

        codes = build_reason_codes(
            f"next_task:score={score:.2f}",
            f"todo_scopes:{len(todo.todo_scopes)}",
            f"active_task:{art.active_task_id or 'none'}",
        )
        return DecisionResult(
            action_kind="show_context_summary",
            confidence=min(0.7 + score * 0.25, 0.9),
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=codes,
        )
