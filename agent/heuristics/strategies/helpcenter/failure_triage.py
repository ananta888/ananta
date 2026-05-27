"""FailureTriageStrategy — helpcenter_failure_triage_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import ArtifactRefPort, CodeCompassReadPort, HeuristicStrategyBase
from agent.heuristics.strategies.scoring import build_reason_codes, keyword_score
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_FAILURE_KEYWORDS = [
    "fail", "failure", "broken", "regression", "flaky", "timeout",
    "assertion", "assert", "test fail", "ci fail", "build fail",
    "pipeline", "exit code", "nonzero",
]
_MIN_SCORE = 0.15


class FailureTriageStrategy(HeuristicStrategyBase):
    """Triage a reported test/CI failure against known helpcenter patterns.

    Detects failure-related queries and opens the most relevant source ref
    from allowed helpcenter scopes. Never invents failure causes. Deterministic.
    """

    def domain(self) -> str:
        return "helpcenter"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        cc = CodeCompassReadPort.from_context(context)
        art = ArtifactRefPort.from_context(context)
        params = definition.parameters or {}
        query = str(context.query or "")
        min_score = float(params.get("min_score", _MIN_SCORE))

        score = keyword_score(query, _FAILURE_KEYWORDS)

        if score < min_score:
            return DecisionResult(
                action_kind="no_action",
                confidence=1.0,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["failure_triage:no_failure_keywords"],
            )

        helpcenter_scopes = [s for s in cc.allowed_source_scopes if "helpcenter" in s.lower()]
        has_refs = bool(helpcenter_scopes or art.selected_artifacts)

        if not has_refs:
            return DecisionResult(
                action_kind="ask_scope",
                confidence=0.7,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["failure_triage:no_helpcenter_scope"],
            )

        codes = build_reason_codes(
            f"failure_triage:score={score:.2f}",
            f"helpcenter_scopes:{len(helpcenter_scopes)}",
        )
        return DecisionResult(
            action_kind="show_context_summary",
            confidence=min(0.65 + score * 0.3, 0.9),
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=codes,
        )
