"""DSL v2 Evaluation Engine — deterministisch, kein LLM, safe."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from agent.services.heuristic_runtime.dsl.expression import evaluate as eval_expr
except ImportError:
    def eval_expr(expr, context):  # type: ignore
        return None

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult


@dataclass
class EvalResult:
    matched: bool
    score: float
    action: dict[str, Any]
    reason_codes: list[str] = field(default_factory=list)
    rejected: bool = False
    reject_reason: str | None = None


class DslEvaluator:
    def evaluate(self, dsl: dict[str, Any], ctx: DecisionContext) -> EvalResult:
        """Wertet DSL gegen DecisionContext aus. Deterministisch, kein LLM-Aufruf."""
        try:
            return self._do_evaluate(dsl, ctx)
        except Exception as e:
            return EvalResult(matched=False, score=0.0, action={"kind": "no_action"},
                            rejected=True, reject_reason=str(e))

    def _do_evaluate(self, dsl: dict[str, Any], ctx: DecisionContext) -> EvalResult:
        ctx_dict = ctx.to_dict() if hasattr(ctx, "to_dict") else {}

        # match
        match_expr = dsl.get("match")
        if match_expr is not None:
            matched = bool(eval_expr(match_expr, ctx_dict))
        else:
            matched = True

        if not matched:
            return EvalResult(matched=False, score=0.0, action={"kind": "no_action"})

        # score
        score_block = dsl.get("score") or {}
        base_score = max(0.0, min(1.0, float(score_block.get("base", 0.8))))
        score_expr = score_block.get("expression")
        if score_expr is not None:
            score_val = eval_expr(score_expr, ctx_dict)
            if isinstance(score_val, (int, float)):
                base_score = max(0.0, min(1.0, float(score_val)))

        # action
        action = dict(dsl.get("action") or {})
        action.setdefault("confidence", base_score)

        return EvalResult(matched=True, score=base_score, action=action)

    def to_decision_result(self, eval_result: EvalResult, strategy_id: str | None = None) -> DecisionResult:
        if not eval_result.matched or eval_result.rejected:
            return DecisionResult.no_good_match()
        try:
            return DecisionResult.from_dsl_action(eval_result.action, strategy_id=strategy_id)
        except Exception:
            return DecisionResult.no_good_match()
