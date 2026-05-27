"""TodoStatusStrategy — chat_codecompass_todo_status_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import HeuristicStrategyBase, TodoReadPort
from agent.heuristics.strategies.scoring import build_reason_codes, keyword_score
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_TODO_KEYWORDS = [
    "todo", "task", "ticket", "issue", "backlog", "sprint", "next",
    "done", "in progress", "blocked", "wip", "status", "assigned",
]
_MIN_SCORE = 0.15


class TodoStatusStrategy(HeuristicStrategyBase):
    """Surface task/todo status from allowed todo/task source scopes.

    Activates when the query contains task-related keywords and there are
    todo source scopes available. Returns a context summary of task status.
    Never fabricates task state — only reports what is in source refs.
    Deterministic.
    """

    def domain(self) -> str:
        return "chat_codecompass"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        todo = TodoReadPort.from_context(context)
        params = definition.parameters or {}
        query = str(getattr(context, "query", "") or "")
        min_score = float(params.get("min_score", _MIN_SCORE))

        score = keyword_score(query, _TODO_KEYWORDS)

        if score < min_score:
            return DecisionResult(
                action_kind="no_action",
                confidence=1.0,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["todo_status:no_task_keywords"],
            )

        if not todo.todo_scopes:
            return DecisionResult(
                action_kind="ask_scope",
                confidence=0.65,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["todo_status:no_todo_scopes"],
            )

        codes = build_reason_codes(
            f"todo_status:score={score:.2f}",
            f"scopes:{len(todo.todo_scopes)}",
        )

        return DecisionResult(
            action_kind="show_context_summary",
            confidence=min(0.6 + score * 0.35, 0.9),
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=codes,
        )
