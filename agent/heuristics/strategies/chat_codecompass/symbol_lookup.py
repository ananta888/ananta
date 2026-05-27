"""SymbolLookupStrategy — chat_codecompass_symbol_lookup_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import CodeCompassReadPort, HeuristicStrategyBase
from agent.heuristics.strategies.scoring import build_reason_codes, keyword_score
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_SYMBOL_INDICATORS = [
    "class", "method", "function", "interface", "enum", "def ", "->",
    "import", "extends", "implements", "override",
]
_MIN_SCORE = 0.2


class SymbolLookupStrategy(HeuristicStrategyBase):
    """Look up a code symbol in CodeCompass source refs.

    Activates when the query contains symbol-like tokens (class/method names,
    import statements, type references). Scores the query against known
    symbol indicators and opens the most relevant source ref. Deterministic.
    """

    def domain(self) -> str:
        return "chat_codecompass"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        cc = CodeCompassReadPort.from_context(context)
        params = definition.parameters or {}
        query = str(getattr(context, "query", "") or "")
        min_score = float(params.get("min_score", _MIN_SCORE))

        score = keyword_score(query, _SYMBOL_INDICATORS)

        if score < min_score or not cc.allowed_source_scopes:
            return DecisionResult(
                action_kind="no_action",
                confidence=1.0,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["symbol_lookup:no_match" if score < min_score else "symbol_lookup:no_scopes"],
            )

        codes = build_reason_codes(
            f"symbol_lookup:score={score:.2f}",
            f"scopes:{len(cc.allowed_source_scopes)}",
        )

        return DecisionResult(
            action_kind="open_source_ref",
            confidence=min(0.6 + score * 0.4, 0.95),
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=codes,
        )
