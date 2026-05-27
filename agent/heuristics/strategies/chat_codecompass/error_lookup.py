"""ErrorLookupStrategy — chat_codecompass_error_lookup_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import ArtifactRefPort, CodeCompassReadPort, HeuristicStrategyBase
from agent.heuristics.strategies.scoring import build_reason_codes, keyword_score
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_ERROR_KEYWORDS = [
    "error", "exception", "stacktrace", "stack trace", "caused by",
    "nullpointerexception", "npe", "classnotfound", "nosuchmethod",
    "syntaxerror", "typeerror", "valueerror", "runtimeexception",
    "fail", "crash", "panic", "fatal",
]
_MIN_SCORE = 0.15


class ErrorLookupStrategy(HeuristicStrategyBase):
    """Look up error/exception context from CodeCompass refs.

    Activates when the query contains error-related keywords. Returns a
    context summary drawn from allowed source scopes. Anti-hallucination:
    never invents error causes — only surfaces what is in source refs.
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
        art = ArtifactRefPort.from_context(context)
        params = definition.parameters or {}
        query = str(getattr(context, "query", "") or "")
        min_score = float(params.get("min_score", _MIN_SCORE))

        score = keyword_score(query, _ERROR_KEYWORDS)

        if score < min_score:
            return DecisionResult(
                action_kind="no_action",
                confidence=1.0,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["error_lookup:no_error_keywords"],
            )

        has_refs = bool(cc.allowed_source_scopes or art.selected_artifacts)
        if not has_refs:
            return DecisionResult(
                action_kind="ask_scope",
                confidence=0.7,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["error_lookup:no_refs_available"],
            )

        codes = build_reason_codes(
            f"error_lookup:score={score:.2f}",
            f"scopes:{len(cc.allowed_source_scopes)}",
        )

        return DecisionResult(
            action_kind="show_context_summary",
            confidence=min(0.65 + score * 0.3, 0.9),
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=codes,
        )
