from __future__ import annotations

import pytest

from ananta_contracts.dspy_optimization import OptimizationBudgets
from tests.dspy_optimization.helpers import binding, price_profiles, program, spec
from worker.optimization.dspy.compatibility import DspyCompatibilityAdapter
from worker.optimization.dspy.engine_adapter import DspyOptimizationEngineAdapter
from worker.optimization.dspy.lm_bridge import (
    AnantaBaseLmBridge,
    DspyBudgetLedger,
    DspyLmCompatibilityBridge,
)


class DeterministicAuthorizedLm:
    def complete(self, **_kwargs):
        return {
            "text": "authorized answer",
            "finish_reason": "stop",
            "usage": {"input_tokens": 2, "output_tokens": 2, "total_tokens": 4, "cache_hit": False},
            "cost_micros": 0,
        }


@pytest.mark.skipif(DspyCompatibilityAdapter().inspect()["state"] != "available", reason="optional dspy extra absent")
def test_real_dspy_321_baselm_legacy_and_typed_seams() -> None:
    events: list[dict] = []
    bridge = DspyLmCompatibilityBridge(
        AnantaBaseLmBridge(
            DeterministicAuthorizedLm(),
            bindings={"student": binding()},
            ledger=DspyBudgetLedger(OptimizationBudgets(3, 20, 0, 30, 1, 10, 10_000)),
            run_id="run-real-compat",
            attempt_id="attempt-real-compat",
            audit_sink=lambda event: events.append(dict(event)),
            price_profiles=price_profiles(),
        ),
        role="student",
    )
    lm = bridge.build_pinned_dspy_lm()
    assert lm(prompt="hello") == ["authorized answer"]
    typed = bridge.complete_typed({"prompt": "typed hello", "role": "student"})
    assert typed["text"] == "authorized answer"
    assert typed["usage"]["total_tokens"] == 4
    assert len(events) == 2
    assert all("prompt" not in event and "output" not in event for event in events)


def test_typed_seam_rejects_role_and_free_parameters() -> None:
    bridge = DspyLmCompatibilityBridge(
        AnantaBaseLmBridge(
            DeterministicAuthorizedLm(),
            bindings={"student": binding()},
            ledger=DspyBudgetLedger(OptimizationBudgets(1, 10, 0, 30, 1, 10, 10_000)),
            run_id="run-typed",
            attempt_id="attempt-typed",
            price_profiles=price_profiles(),
        ),
        role="student",
    )
    with pytest.raises(ValueError, match="typed_lm_request_invalid"):
        bridge.complete_typed({"prompt": "x", "role": "teacher"})
    with pytest.raises(ValueError, match="parameter_denied"):
        bridge.complete_typed({"prompt": "x", "parameters": {"api_base": "https://evil.invalid"}})


@pytest.mark.skipif(DspyCompatibilityAdapter().inspect()["state"] != "available", reason="optional dspy extra absent")
def test_real_dspy_321_labeled_optimizer_exports_only_closed_state() -> None:
    candidate = DspyOptimizationEngineAdapter(optimizer_config={"max_labeled_demos": 1}).optimize(
        spec(),
        program(),
        [{"goal": "Build", "constraints": "none", "tasks": '[{"id":"T1"}]'}],
    )
    assert candidate.exporter_version == "dspy-json-v1:3.2.1"
    assert candidate.demonstrations[0]["goal"] == "Build"
    assert "api_key" not in str(candidate.to_dict())
