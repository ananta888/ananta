from __future__ import annotations

import pytest

from ananta_contracts.dspy_optimization import OptimizationBudgets
from tests.dspy_optimization.helpers import binding, price_profiles
from worker.optimization.dspy.lm_bridge import AnantaBaseLmBridge, DspyBudgetExceeded, DspyBudgetLedger


class FakeLm:
    def complete(self, **_kwargs):
        return {"text": "ok", "finish_reason": "stop", "usage": {"total_tokens": 5}, "cost_micros": 0}


def test_lm_bridge_enforces_exact_role_binding_and_monotone_usage() -> None:
    ledger = DspyBudgetLedger(OptimizationBudgets(2, 10, 0, 30, 1, 10, 10_000))
    bridge = AnantaBaseLmBridge(
        FakeLm(), bindings={"student": binding()}, ledger=ledger, run_id="run-1", attempt_id="attempt-1",
        price_profiles=price_profiles(),
    )
    result = bridge.complete(role="student", messages=[{"role": "user", "content": "hello"}], call_index=0)
    assert result["text"] == "ok"
    assert ledger.snapshot() == {"model_calls": 1, "tokens": 5, "cost_micros": 0}
    with pytest.raises(DspyBudgetExceeded, match="replay_denied"):
        bridge.complete(role="student", messages=[{"role": "user", "content": "hello"}], call_index=0)
    with pytest.raises(PermissionError, match="role_not_authorized"):
        bridge.complete(role="teacher", messages=[{"role": "user", "content": "hello"}], call_index=1)


def test_missing_usage_fails_closed_instead_of_becoming_zero_cost() -> None:
    class MissingUsage:
        def complete(self, **_kwargs):
            return {"text": "unsafe"}

    bridge = AnantaBaseLmBridge(
        MissingUsage(),
        bindings={"student": binding()},
        ledger=DspyBudgetLedger(OptimizationBudgets(1, 10, 0, 30, 1, 10, 10_000)),
        run_id="run-1",
        attempt_id="attempt-1",
        price_profiles=price_profiles(),
    )
    with pytest.raises(DspyBudgetExceeded, match="usage_missing"):
        bridge.complete(role="student", messages=[{"role": "user", "content": "hello"}], call_index=0)


def test_missing_ananta_price_profile_fails_closed_instead_of_trusting_provider_cost() -> None:
    class MissingCost:
        def complete(self, **_kwargs):
            return {"text": "unsafe", "usage": {"total_tokens": 1}}

    bridge = AnantaBaseLmBridge(
        MissingCost(),
        bindings={"student": binding()},
        ledger=DspyBudgetLedger(OptimizationBudgets(1, 10, 0, 30, 1, 10, 10_000)),
        run_id="run-1",
        attempt_id="attempt-1",
    )
    with pytest.raises(DspyBudgetExceeded, match="price_profile_missing"):
        bridge.complete(role="student", messages=[{"role": "user", "content": "hello"}], call_index=0)


def test_ananta_price_profile_calculates_cost_and_keeps_provider_cost_observational() -> None:
    class Priced:
        def complete(self, **_kwargs):
            return {
                "text": "ok",
                "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                "cost_micros": 999_999,
            }

    profile = price_profiles()
    profile[binding()["binding_id"]] = {
        "input_micros_per_million": 1_000_000,
        "output_micros_per_million": 2_000_000,
        "reasoning_micros_per_million": 0,
    }
    ledger = DspyBudgetLedger(OptimizationBudgets(1, 10, 8, 30, 1, 10, 10_000))
    result = AnantaBaseLmBridge(
        Priced(), bindings={"student": binding()}, ledger=ledger, run_id="run-1", attempt_id="attempt-1",
        price_profiles=profile,
    ).complete(role="student", messages=[{"role": "user", "content": "hello"}], call_index=0)
    assert result["cost_micros"] == 8
    assert result["observed_provider_cost_micros"] == 999_999
    assert ledger.snapshot()["cost_micros"] == 8
