"""ArchiveDoneStrategy — planning_archive_done_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import HeuristicStrategyBase, TodoReadPort
from agent.heuristics.strategies.scoring import build_reason_codes, keyword_score
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_ARCHIVE_KEYWORDS = [
    "archive", "clean", "cleanup", "done", "finished", "completed",
    "remove", "clear", "tidy", "prune",
]
_MIN_SCORE = 0.1


class ArchiveDoneStrategy(HeuristicStrategyBase):
    """Suggest archiving completed tasks when todo scopes are cluttered.

    Activates when query references cleanup/archiving of done tasks. Shows
    a context summary of which tasks can be archived. Never deletes anything
    autonomously. Deterministic.
    """

    def domain(self) -> str:
        return "planning"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        todo = TodoReadPort.from_context(context)
        params = definition.parameters or {}
        query = str(context.query or "")
        min_score = float(params.get("min_score", _MIN_SCORE))

        score = keyword_score(query, _ARCHIVE_KEYWORDS)

        if score < min_score:
            return DecisionResult(
                action_kind="no_action",
                confidence=1.0,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["archive_done:no_archive_keywords"],
            )

        if not todo.todo_scopes:
            return DecisionResult(
                action_kind="ask_scope",
                confidence=0.6,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["archive_done:no_todo_scopes"],
            )

        codes = build_reason_codes(
            f"archive_done:score={score:.2f}",
            f"todo_scopes:{len(todo.todo_scopes)}",
        )
        return DecisionResult(
            action_kind="show_context_summary",
            confidence=min(0.65 + score * 0.3, 0.88),
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=codes,
        )
