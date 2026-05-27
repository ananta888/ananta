"""Strategy Pattern for heuristic decision-making per surface domain.

Three concrete strategies:
  DefaultTuiSnakeStrategy      — TUI snake: follow + lurk + zone fallback
  DefaultEclipseSnakeStrategy  — Eclipse plugin snake: zone-based motion
  DefaultChatCodeCompassStrategy — Chat: context selection

All strategies accept DecisionContext + list[HeuristicDefinition] and return DecisionResult.
Selection of the best HeuristicDefinition for execution is delegated to the registry;
the strategy encodes HOW to interpret and apply the chosen heuristic.
"""
from __future__ import annotations

import abc
from typing import Any

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition


class HeuristicStrategy(abc.ABC):
    """Abstract base: maps (context, candidates) → DecisionResult."""

    @property
    @abc.abstractmethod
    def domain(self) -> str: ...

    @abc.abstractmethod
    def decide(
        self,
        ctx: DecisionContext,
        candidates: list[HeuristicDefinition],
    ) -> DecisionResult: ...

    # ── shared helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _best_candidate(candidates: list[HeuristicDefinition]) -> HeuristicDefinition | None:
        if not candidates:
            return None
        return max(candidates, key=lambda h: (
            1 if h.deterministic else 0,
            _SAFETY_RANK.get(h.safety_class, 0),
        ))

    @staticmethod
    def _no_candidates() -> DecisionResult:
        return DecisionResult.no_good_match()


_SAFETY_RANK: dict[str, int] = {"safety_critical": 3, "bounded": 2, "low_risk": 1}


# ── TUI Snake ─────────────────────────────────────────────────────────────────

class DefaultTuiSnakeStrategy(HeuristicStrategy):
    """Default strategy for tui_snake surface.

    Priority:
      1. follow — if active_goal_id present (cursor chase mode)
      2. lurk   — if no goal (idle mode)
      3. follow fallback — any follow candidate with fallback flag
    """

    @property
    def domain(self) -> str:
        return "tui_snake"

    def decide(self, ctx: DecisionContext, candidates: list[HeuristicDefinition]) -> DecisionResult:
        if not candidates:
            return self._no_candidates()

        follow = [h for h in candidates if h.strategy_kind in ("follow", "follow_with_distance")]
        lurk = [h for h in candidates if h.strategy_kind in ("lurk", "patrol")]

        if ctx.active_goal_id:
            chosen = self._best_candidate(follow) or self._best_candidate(candidates)
            assert chosen is not None
            return DecisionResult.heuristic_follow(dx=0, dy=0, strategy_id=chosen.heuristic_id)

        if lurk:
            chosen = self._best_candidate(lurk)
            assert chosen is not None
            return DecisionResult.heuristic_lurk(strategy_id=chosen.heuristic_id)

        chosen = self._best_candidate(candidates)
        assert chosen is not None
        return DecisionResult.fallback(
            reason="no_matching_strategy_kind",
            strategy_id=chosen.heuristic_id,
        )


# ── Eclipse Snake ─────────────────────────────────────────────────────────────

class DefaultEclipseSnakeStrategy(HeuristicStrategy):
    """Default strategy for eclipse_snake surface.

    Zone-based: uses active_panel to determine motion direction.
    Falls back to lurk when no panel context is available.
    """

    @property
    def domain(self) -> str:
        return "eclipse_snake"

    def decide(self, ctx: DecisionContext, candidates: list[HeuristicDefinition]) -> DecisionResult:
        if not candidates:
            return self._no_candidates()

        chosen = self._best_candidate(candidates)
        assert chosen is not None

        if ctx.active_panel:
            dx, dy = _panel_to_motion(ctx.active_panel)
            return DecisionResult.heuristic_follow(dx=dx, dy=dy, strategy_id=chosen.heuristic_id)

        return DecisionResult.heuristic_lurk(strategy_id=chosen.heuristic_id)


_PANEL_MOTION: dict[str, tuple[int, int]] = {
    "editor": (1, 0),
    "explorer": (-1, 0),
    "terminal": (0, 1),
    "outline": (0, -1),
    "problems": (1, 1),
}


def _panel_to_motion(panel: str) -> tuple[int, int]:
    return _PANEL_MOTION.get(panel.lower().strip(), (0, 0))


# ── Chat CodeCompass ──────────────────────────────────────────────────────────

class DefaultChatCodeCompassStrategy(HeuristicStrategy):
    """Default strategy for chat_codecompass surface.

    Selects context references from selected_artifacts and emits a context_summary answer.
    Falls back to no_good_match when no artifacts are selected.
    """

    @property
    def domain(self) -> str:
        return "chat_codecompass"

    def decide(self, ctx: DecisionContext, candidates: list[HeuristicDefinition]) -> DecisionResult:
        if not candidates:
            return self._no_candidates()

        chosen = self._best_candidate(candidates)
        assert chosen is not None

        if not ctx.selected_artifacts:
            return DecisionResult.no_good_match()

        return DecisionResult(
            action_kind="chat",
            confidence=0.9,
            source="heuristic",
            answer_kind="context_summary",
            selected_context_refs=list(ctx.selected_artifacts[:5]),
            strategy_id=chosen.heuristic_id,
            reason_codes=["artifact_context_selected"],
        )


# ── Registry ──────────────────────────────────────────────────────────────────

_STRATEGIES: dict[str, HeuristicStrategy] = {
    s.domain: s
    for s in [
        DefaultTuiSnakeStrategy(),
        DefaultEclipseSnakeStrategy(),
        DefaultChatCodeCompassStrategy(),
    ]
}


def get_strategy(domain: str) -> HeuristicStrategy | None:
    """Return the registered strategy for a domain, or None if unknown."""
    return _STRATEGIES.get(domain)


def decide_for_context(
    ctx: DecisionContext,
    candidates: list[HeuristicDefinition],
) -> DecisionResult:
    """Convenience: look up strategy by domain and execute decide()."""
    strategy = get_strategy(ctx.source_surface)
    if strategy is None:
        return DecisionResult.fallback(
            reason=f"no_strategy_for_domain:{ctx.source_surface}",
        )
    return strategy.decide(ctx, candidates)
