"""SourcePackLookupStrategy — chat_codecompass_sourcepack_lookup_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import CodeCompassReadPort, HeuristicStrategyBase
from agent.heuristics.strategies.scoring import build_reason_codes, weighted_rank
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_TECHNICAL_KEYWORDS = (
    "api", "service", "repository", "config", "schema", "model",
    "controller", "handler", "util", "helper", "type", "interface",
)


class SourcePackLookupStrategy(HeuristicStrategyBase):
    """Pick the most query-relevant sourcepack from allowed source scopes.

    Ranks available source scopes by how closely their names match technical
    keywords in the query. Returns an open_source_ref action for the top
    ranked scope. Falls back to no_action when no scopes are available.
    Deterministic.
    """

    def domain(self) -> str:
        return "chat_codecompass"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        cc = CodeCompassReadPort.from_context(context)
        query = str(getattr(context, "query", "") or "").lower()

        if not cc.allowed_source_scopes:
            return DecisionResult(
                action_kind="no_action",
                confidence=1.0,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["sourcepack_lookup:no_scopes"],
            )

        # Rank scopes by how many technical keywords from the query they contain
        weights = {
            scope: sum(1.0 for kw in _TECHNICAL_KEYWORDS if kw in scope.lower() or kw in query)
            for scope in cc.allowed_source_scopes
        }
        ranked = weighted_rank(list(cc.allowed_source_scopes), weights)
        top_scope, top_score = ranked[0]

        codes = build_reason_codes(
            f"sourcepack_lookup:scope={top_scope[:40]}",
            f"rank_score={top_score:.1f}",
        )

        return DecisionResult(
            action_kind="open_source_ref",
            confidence=0.75,
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=codes,
        )
