"""Controlled before/after comparison for permission-neutral role overlays."""

from __future__ import annotations

from typing import Iterable

from ananta_contracts.cognitive_style import (
    RoleStyleOverlay,
    StyleOverlayComparisonCommand,
    StyleOverlayComparisonReport,
)
from ananta_contracts.model_selection import AgentStyleProfile


class CognitiveStyleOverlayComparisonService:
    def compare(
        self,
        *,
        command: StyleOverlayComparisonCommand,
        profiles: Iterable[AgentStyleProfile],
        overlays: Iterable[RoleStyleOverlay],
    ) -> StyleOverlayComparisonReport:
        by_id = {item.profile_id: item for item in profiles}
        baseline = by_id.get(command.baseline_profile_id)
        calibrated = by_id.get(command.overlay_profile_id)
        overlay = next(
            (item for item in overlays if item.overlay_id == command.overlay_id),
            None,
        )
        common = {
            "baseline_profile_id": command.baseline_profile_id,
            "overlay_profile_id": command.overlay_profile_id,
            "overlay_id": command.overlay_id,
        }
        if baseline is None or calibrated is None or overlay is None:
            return StyleOverlayComparisonReport(
                **common, comparable=False,
                reason_codes=("style_overlay_comparison_input_missing",),
            )
        context = lambda item: (
            item.model_profile_id, item.model_revision, item.quantization,
            item.runtime, item.backend_id, item.tool_mode, item.sampling_digest,
            item.benchmark_revision, item.prompt_digest,
        )
        if context(baseline) != context(calibrated):
            return StyleOverlayComparisonReport(
                **common, comparable=False,
                reason_codes=("style_overlay_measurement_context_mismatch",),
            )
        deltas = {
            name: round(
                getattr(calibrated.scores, name) - getattr(baseline.scores, name), 6
            )
            for name in (
                "rule_correctness", "truth_exploration", "initiative_assertiveness"
            )
        }
        improved = tuple(name for name in overlay.reinforces if deltas[name] > 0)
        regressed = tuple(name for name in overlay.reinforces if deltas[name] < 0)
        reasons = (
            ("style_overlay_reinforcement_observed",)
            if improved and not regressed
            else ("style_overlay_reinforcement_not_observed",)
        )
        return StyleOverlayComparisonReport(
            **common,
            comparable=True,
            reason_codes=reasons,
            score_deltas=deltas,
            reinforced_dimensions_improved=improved,
            reinforced_dimensions_regressed=regressed,
        )


__all__ = ["CognitiveStyleOverlayComparisonService"]
