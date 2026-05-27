"""EclipsePackageExplorerStrategy — snake_eclipse_package_explorer_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import ArtifactRefPort, HeuristicStrategyBase, UiMotionPort
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_EXPLORER_KEYWORDS = ("package_explorer", "package", "project_explorer", "navigator", "explorer")


class EclipsePackageExplorerStrategy(HeuristicStrategyBase):
    """Lurk near selected package/file in Eclipse Package Explorer.

    When the Package Explorer is active, stays close to the selected node
    rather than following cursor movement in the editor. Deterministic.
    """

    def domain(self) -> str:
        return "snake_eclipse"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        ui = UiMotionPort.from_context(context)
        art = ArtifactRefPort.from_context(context)
        panel = (ui.active_panel or "").lower()

        in_explorer = any(kw in panel for kw in _EXPLORER_KEYWORDS)

        if not in_explorer:
            return DecisionResult.fallback(
                reason="not_in_package_explorer",
                strategy_id=self.strategy_id,
            )

        zone = ui.active_panel or "package_explorer"
        selected = art.selected_artifacts[0][:32] if art.selected_artifacts else ""

        codes = [f"eclipse_pkg_explorer:{zone}"]
        if selected:
            codes.append(f"selected:{selected}")

        return DecisionResult(
            action_kind="lurk",
            confidence=0.85,
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=codes,
        )
