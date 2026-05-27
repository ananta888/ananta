"""Chat-specific selector chain elements (Chain of Responsibility pattern).

Selectors run in priority order and return the best context match.
Each selector inspects DecisionContext and query-derived signals.
"""
from __future__ import annotations

from agent.services.heuristic_runtime.chain import ChainResult, HeuristicRuleChainElement
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult

_TODO_KEYWORDS = frozenset({"todo", "aufgabe", "task", "backlog", "offene", "open tasks"})
_SYMBOL_KEYWORDS = frozenset({"wo ist", "where is", "find", "symbol", "function", "class", "method", "def ", "klasse"})
_FILE_KEYWORDS = frozenset({"datei", "file", "modul", "module", "script", "open file"})
_HELPCENTER_KEYWORDS = frozenset({"help", "hilfe", "anleitung", "guide", "faq", "documentation", "how to", "wie geht"})
_SOURCEPACK_KEYWORDS = frozenset({"sourcepack", "quellpaket", "codebase", "repository", "repo", "project sources"})


def _lower_query(ctx: DecisionContext) -> str:
    # query is passed through metadata if available; fallback empty
    return str(ctx.recent_events[-1].get("normalized_value", "") if ctx.recent_events else "").lower()


class SelectedArtifactSelector(HeuristicRuleChainElement):
    """Prio 1: If artifacts are selected, return context_summary immediately."""

    priority = 1

    def handle(self, ctx: DecisionContext, result: DecisionResult | None) -> ChainResult:
        if ctx.selected_artifacts:
            return ChainResult.handled(
                DecisionResult(
                    action_kind="chat",
                    confidence=1.0,
                    source="heuristic",
                    answer_kind="context_summary",
                    selected_context_refs=list(ctx.selected_artifacts[:5]),
                    reason_codes=["selected_artifact"],
                    strategy_id="selected_artifact_selector",
                ),
                rule_id=self.rule_id,
            )
        return ChainResult.abstain(rule_id=self.rule_id, reason="no_selected_artifacts")


class ActiveGoalSelector(HeuristicRuleChainElement):
    """Prio 2: Active goal present — include goal context reference."""

    priority = 2

    def handle(self, ctx: DecisionContext, result: DecisionResult | None) -> ChainResult:
        if ctx.active_goal_id:
            refs = [f"goal:{ctx.active_goal_id}"]
            if ctx.active_task_id:
                refs.append(f"task:{ctx.active_task_id}")
            return ChainResult.handled(
                DecisionResult(
                    action_kind="chat",
                    confidence=0.9,
                    source="heuristic",
                    answer_kind="context_summary",
                    selected_context_refs=refs,
                    reason_codes=["active_goal"],
                    strategy_id="active_goal_selector",
                ),
                rule_id=self.rule_id,
            )
        return ChainResult.abstain(rule_id=self.rule_id, reason="no_active_goal")


class ErrorHelpcenterSelector(HeuristicRuleChainElement):
    """Prio 3: Recent error event → suggest helpcenter context."""

    priority = 3

    def handle(self, ctx: DecisionContext, result: DecisionResult | None) -> ChainResult:
        for ev in reversed(ctx.recent_events):
            if ev.get("kind") in ("error_detected", "build_error", "exception"):
                return ChainResult.handled(
                    DecisionResult(
                        action_kind="chat",
                        confidence=0.85,
                        source="heuristic",
                        answer_kind="heuristic_answer",
                        reason_codes=["error_event_detected", "helpcenter_lookup"],
                        strategy_id="error_helpcenter_selector",
                    ),
                    rule_id=self.rule_id,
                )
        return ChainResult.abstain(rule_id=self.rule_id, reason="no_error_event")


class SymbolSelector(HeuristicRuleChainElement):
    """Prio 4: Query contains symbol-lookup keywords."""

    priority = 4

    def handle(self, ctx: DecisionContext, result: DecisionResult | None) -> ChainResult:
        q = _lower_query(ctx)
        if any(kw in q for kw in _SYMBOL_KEYWORDS):
            return ChainResult.handled(
                DecisionResult(
                    action_kind="chat",
                    confidence=0.75,
                    source="heuristic",
                    answer_kind="source_ref",
                    reason_codes=["symbol_keyword_match"],
                    strategy_id="symbol_selector",
                ),
                rule_id=self.rule_id,
            )
        return ChainResult.abstain(rule_id=self.rule_id, reason="no_symbol_keyword")


class FileSelector(HeuristicRuleChainElement):
    """Prio 5: Query contains file-reference keywords."""

    priority = 5

    def handle(self, ctx: DecisionContext, result: DecisionResult | None) -> ChainResult:
        q = _lower_query(ctx)
        if any(kw in q for kw in _FILE_KEYWORDS):
            return ChainResult.handled(
                DecisionResult(
                    action_kind="chat",
                    confidence=0.7,
                    source="heuristic",
                    answer_kind="source_ref",
                    reason_codes=["file_keyword_match"],
                    strategy_id="file_selector",
                ),
                rule_id=self.rule_id,
            )
        return ChainResult.abstain(rule_id=self.rule_id, reason="no_file_keyword")


class TodoSelector(HeuristicRuleChainElement):
    """Prio 6: Query about task/todo status."""

    priority = 6

    def handle(self, ctx: DecisionContext, result: DecisionResult | None) -> ChainResult:
        q = _lower_query(ctx)
        if any(kw in q for kw in _TODO_KEYWORDS):
            return ChainResult.handled(
                DecisionResult(
                    action_kind="chat",
                    confidence=0.8,
                    source="heuristic",
                    answer_kind="heuristic_answer",
                    reason_codes=["todo_keyword_match"],
                    strategy_id="todo_selector",
                ),
                rule_id=self.rule_id,
            )
        return ChainResult.abstain(rule_id=self.rule_id, reason="no_todo_keyword")


class SourcepackSelector(HeuristicRuleChainElement):
    """Prio 7: Query references the codebase/sourcepack."""

    priority = 7

    def handle(self, ctx: DecisionContext, result: DecisionResult | None) -> ChainResult:
        q = _lower_query(ctx)
        if any(kw in q for kw in _SOURCEPACK_KEYWORDS):
            return ChainResult.handled(
                DecisionResult(
                    action_kind="chat",
                    confidence=0.65,
                    source="heuristic",
                    answer_kind="source_ref",
                    reason_codes=["sourcepack_keyword_match"],
                    strategy_id="sourcepack_selector",
                ),
                rule_id=self.rule_id,
            )
        return ChainResult.abstain(rule_id=self.rule_id, reason="no_sourcepack_keyword")


class NoMatchSelector(HeuristicRuleChainElement):
    """Prio 99: Always-handled fallback — no good context match found."""

    priority = 99

    def handle(self, ctx: DecisionContext, result: DecisionResult | None) -> ChainResult:
        return ChainResult.handled(
            DecisionResult.no_good_match(),
            rule_id=self.rule_id,
            reason="no_selector_matched",
        )


def build_chat_selector_chain() -> "RuleChain":
    from agent.services.heuristic_runtime.chain import RuleChain
    return RuleChain([
        SelectedArtifactSelector(),
        ActiveGoalSelector(),
        ErrorHelpcenterSelector(),
        SymbolSelector(),
        FileSelector(),
        TodoSelector(),
        SourcepackSelector(),
        NoMatchSelector(),
    ])
