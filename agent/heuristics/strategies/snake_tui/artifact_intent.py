"""TuiArtifactIntentStrategy — implements snake_tui_artifact_intent_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import (
    ArtifactRefPort,
    HeuristicStrategyBase,
    UiMotionPort,
)
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult, SuggestedMotion
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_ARTIFACT_EVENTS = frozenset({"artifact_selected", "artifact_highlighted", "artifact_focused"})


class TuiArtifactIntentStrategy(HeuristicStrategyBase):
    """Follow selected/highlighted artifact with intent-aware motion.

    When an artifact is selected or highlighted, derives motion toward the
    artifact's inferred panel position. Falls back to lurk when no artifact
    is active. Deterministic: no AI calls.
    """

    def domain(self) -> str:
        return "tui_snake"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        art = ArtifactRefPort.from_context(context)
        ui = UiMotionPort.from_context(context)
        params = definition.parameters or {}

        has_artifact = bool(art.selected_artifacts)
        has_artifact_event = bool(_ARTIFACT_EVENTS & set(ui.recent_event_types))

        if not has_artifact and not has_artifact_event:
            return DecisionResult.heuristic_lurk(strategy_id=self.strategy_id)

        # Deterministic motion: hash first artifact to get direction
        primary = art.selected_artifacts[0] if art.selected_artifacts else ""
        dx, dy = self._intent_motion(primary, params)

        return DecisionResult(
            action_kind="follow",
            confidence=0.9,
            source="heuristic",
            suggested_motion=SuggestedMotion(dx=dx, dy=dy),
            strategy_id=self.strategy_id,
            reason_codes=[f"artifact_intent:{primary[:32]}"],
        )

    def _intent_motion(self, artifact_ref: str, params: dict) -> tuple[int, int]:
        # Direction based on artifact hash; real impl would use panel positions
        prefer_x = bool(params.get("prefer_horizontal", True))
        h = hash(artifact_ref) % 4
        vectors = [(1, 0), (0, 1), (-1, 0), (0, -1)]
        if prefer_x:
            vectors = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        return vectors[h]
