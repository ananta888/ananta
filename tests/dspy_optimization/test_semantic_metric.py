from __future__ import annotations

from ananta_contracts.dspy_optimization import OptimizationBudgets
from tests.dspy_optimization.helpers import binding, price_profiles
from worker.optimization.dspy.lm_bridge import AnantaBaseLmBridge, DspyBudgetLedger
from worker.optimization.dspy.metric_bridge import DspySemanticJudgeMetricBridge


class JudgePort:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, **_kwargs):
        self.calls += 1
        return {
            "text": '{"score":0.9,"reason_codes":[]}',
            "finish_reason": "stop",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            "cost_micros": 0,
        }


def test_semantic_judge_uses_separate_authorized_role_and_cannot_override_red_gate() -> None:
    port = JudgePort()
    lm = AnantaBaseLmBridge(
        port,
        bindings={"judge": binding()},
        ledger=DspyBudgetLedger(OptimizationBudgets(2, 100, 0, 30, 1, 10, 10_000)),
        run_id="run-1",
        attempt_id="attempt-1",
        price_profiles=price_profiles(),
    )
    service = DspySemanticJudgeMetricBridge(lm)
    red = service.evaluate(
        deterministic={"passed": False}, expected={"answer": "x"}, actual={"answer": "x"}, call_index=0
    )
    assert red["passed"] is False
    assert red["model_call_performed"] is False
    assert port.calls == 0
    green = service.evaluate(
        deterministic={"passed": True}, expected={"answer": "x"}, actual={"answer": "x"}, call_index=1
    )
    assert green["passed"] is True
    assert port.calls == 1
