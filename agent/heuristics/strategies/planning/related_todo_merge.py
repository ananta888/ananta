"""RelatedTodoMergeStrategy — planning_related_todo_merge_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import ArtifactRefPort, HeuristicStrategyBase, TodoReadPort
from agent.heuristics.strategies.scoring import build_reason_codes, keyword_score
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_MERGE_KEYWORDS = [
    "merge", "combine", "consolidate", "duplicate", "related", "overlap",
    "similar task", "same goal", "deduplicate", "group",
]
_MIN_SCORE = 0.1


class RelatedTodoMergeStrategy(HeuristicStrategyBase):
    """Identify related TODOs that could be merged or consolidated.

    Activates when the query asks about merging or consolidating tasks.
    Surfaces todo context for human review. Never merges autonomously.
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

        score = keyword_score(query, _MERGE_KEYWORDS)

        if score < min_score:
            return DecisionResult(
                action_kind="no_action",
                confidence=1.0,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["related_todo_merge:no_merge_keywords"],
            )

        if not todo.todo_scopes:
            return DecisionResult(
                action_kind="ask_scope",
                confidence=0.6,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["related_todo_merge:no_todo_scopes"],
            )

        codes = build_reason_codes(
            f"related_todo_merge:score={score:.2f}",
            f"todo_scopes:{len(todo.todo_scopes)}",
            f"selected:{len(art.selected_artifacts)}",
        )
        return DecisionResult(
            action_kind="show_context_summary",
            confidence=min(0.62 + score * 0.3, 0.87),
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=codes,
        )
