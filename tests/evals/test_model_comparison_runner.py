from __future__ import annotations

import pytest

from agent.evals.model_comparison_runner import (
    ModelComparisonError,
    ModelComparisonObservation,
    evaluate_paired_observations,
)


def observations():
    return [
        ModelComparisonObservation(model, "case-a", repeat, model == "base", False, True, False, 1.0)
        for model in ("base", "candidate")
        for repeat in range(1, 6)
    ]


def test_quality_and_refusal_are_separate_metrics() -> None:
    result = evaluate_paired_observations(observations(), expected_model_ids=("base", "candidate"))
    assert result["metrics"]["base"]["correct_rate"] == 1.0
    assert result["metrics"]["candidate"]["correct_rate"] == 0.0
    assert result["metrics"]["candidate"]["refusal_rate"] == 0.0


def test_missing_pair_or_duplicate_fails_closed() -> None:
    with pytest.raises(ModelComparisonError, match="unpaired"):
        evaluate_paired_observations(observations()[:-1], expected_model_ids=("base", "candidate"))
    with pytest.raises(ModelComparisonError, match="duplicate"):
        evaluate_paired_observations([*observations(), observations()[0]], expected_model_ids=("base", "candidate"))
