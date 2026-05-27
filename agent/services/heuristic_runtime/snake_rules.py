"""Snake-specific rule chain elements (Chain of Responsibility pattern).

Priority order:
  1  ArtifactHoverRule   — artifact hover event detected
  2  DiffFocusRule       — active panel is diff/editor
  3  ChatFocusRule       — surface is chat (not snake) → abstain
  4  ErrorFocusRule      — error event in recent_events
  5  IdleLurkRule        — no active goal → lurk
  99 DefaultFollowRule   — always handled, follow fallback
"""
from __future__ import annotations

from agent.services.heuristic_runtime.chain import ChainResult, HeuristicRuleChainElement
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult

_DIFF_PANELS = frozenset({"diff", "editor", "compare"})
_ERROR_EVENT_KINDS = frozenset({"error_detected", "build_error", "lint_error", "exception"})


class ArtifactHoverRule(HeuristicRuleChainElement):
    """Prio 1: If a recent artifact_select or hover event exists, follow toward it."""

    priority = 1

    def handle(self, ctx: DecisionContext, result: DecisionResult | None) -> ChainResult:
        for ev in reversed(ctx.recent_events):
            if ev.get("kind") in ("artifact_select", "artifact_hover", "pointer_move"):
                return ChainResult.handled(
                    DecisionResult.heuristic_follow(dx=1, dy=0, strategy_id="artifact_hover"),
                    rule_id=self.rule_id,
                    reason="artifact_event_in_recent",
                )
        return ChainResult.abstain(rule_id=self.rule_id, reason="no_artifact_event")


class DiffFocusRule(HeuristicRuleChainElement):
    """Prio 2: If focused panel is a diff/editor view, follow horizontally."""

    priority = 2

    def handle(self, ctx: DecisionContext, result: DecisionResult | None) -> ChainResult:
        panel = (ctx.active_panel or "").lower().strip()
        if panel in _DIFF_PANELS:
            return ChainResult.handled(
                DecisionResult.heuristic_follow(dx=1, dy=0, strategy_id="diff_focus"),
                rule_id=self.rule_id,
                reason=f"panel={panel}",
            )
        return ChainResult.abstain(rule_id=self.rule_id, reason=f"panel={panel}_not_diff")


class ChatFocusRule(HeuristicRuleChainElement):
    """Prio 3: If the surface is chat (not snake), this rule has no opinion → abstain."""

    priority = 3

    def handle(self, ctx: DecisionContext, result: DecisionResult | None) -> ChainResult:
        if "chat" in ctx.source_surface:
            return ChainResult.abstain(rule_id=self.rule_id, reason="chat_surface_not_snake")
        return ChainResult.continue_(rule_id=self.rule_id)


class ErrorFocusRule(HeuristicRuleChainElement):
    """Prio 4: If a recent error event exists, follow toward the error panel."""

    priority = 4

    def handle(self, ctx: DecisionContext, result: DecisionResult | None) -> ChainResult:
        for ev in reversed(ctx.recent_events):
            if ev.get("kind") in _ERROR_EVENT_KINDS:
                return ChainResult.handled(
                    DecisionResult.heuristic_follow(dx=0, dy=1, strategy_id="error_focus"),
                    rule_id=self.rule_id,
                    reason="error_event_detected",
                )
        return ChainResult.abstain(rule_id=self.rule_id, reason="no_error_event")


class IdleLurkRule(HeuristicRuleChainElement):
    """Prio 5: If no active goal is set, enter lurk mode."""

    priority = 5

    def handle(self, ctx: DecisionContext, result: DecisionResult | None) -> ChainResult:
        if not ctx.active_goal_id:
            return ChainResult.handled(
                DecisionResult.heuristic_lurk(strategy_id="idle_lurk"),
                rule_id=self.rule_id,
                reason="no_active_goal",
            )
        return ChainResult.abstain(rule_id=self.rule_id, reason="goal_active")


class DefaultFollowRule(HeuristicRuleChainElement):
    """Prio 99: Always handled — follow with zero delta as safe default."""

    priority = 99

    def handle(self, ctx: DecisionContext, result: DecisionResult | None) -> ChainResult:
        return ChainResult.handled(
            DecisionResult.heuristic_follow(dx=0, dy=0, strategy_id="default_follow"),
            rule_id=self.rule_id,
            reason="default_fallback",
        )


def build_snake_rule_chain() -> "RuleChain":
    from agent.services.heuristic_runtime.chain import RuleChain
    return RuleChain([
        ArtifactHoverRule(),
        DiffFocusRule(),
        ChatFocusRule(),
        ErrorFocusRule(),
        IdleLurkRule(),
        DefaultFollowRule(),
    ])
