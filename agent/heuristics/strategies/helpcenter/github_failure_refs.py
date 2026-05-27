"""GithubFailureSourceRefsStrategy — helpcenter_github_failure_source_refs_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import ArtifactRefPort, CodeCompassReadPort, HeuristicStrategyBase
from agent.heuristics.strategies.scoring import build_reason_codes, keyword_score
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_GITHUB_KEYWORDS = [
    "pr", "pull request", "issue", "commit", "sha", "branch", "merge",
    "github", "workflow", "action", "check run", "status check",
]
_MIN_SCORE = 0.1


class GithubFailureSourceRefsStrategy(HeuristicStrategyBase):
    """Look up GitHub failure context from CodeCompass source refs.

    Activates on queries referencing GitHub entities (PRs, issues, commits,
    workflow runs). Opens the most relevant source ref. Deterministic.
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

        score = keyword_score(query, _GITHUB_KEYWORDS)

        if score < min_score:
            return DecisionResult(
                action_kind="no_action",
                confidence=1.0,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["github_failure_refs:no_github_keywords"],
            )

        github_scopes = [s for s in cc.allowed_source_scopes if "github" in s.lower() or "git" in s.lower()]
        has_refs = bool(github_scopes or art.selected_artifacts)

        if not has_refs:
            return DecisionResult(
                action_kind="ask_scope",
                confidence=0.65,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["github_failure_refs:no_github_scope"],
            )

        codes = build_reason_codes(
            f"github_failure_refs:score={score:.2f}",
            f"github_scopes:{len(github_scopes)}",
        )
        return DecisionResult(
            action_kind="open_source_ref",
            confidence=min(0.7 + score * 0.25, 0.9),
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=codes,
        )
