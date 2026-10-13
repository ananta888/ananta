"""Ambiguous output cannot count as a correct specialist decision."""

import pytest

from agent.services.specialist_benchmark import classification_metrics, predictions_from_outputs
from ananta_contracts.specialist_decision import SpecialistContractError, builtin_contracts

GATE = builtin_contracts()["code-risk-gate"]


@pytest.mark.parametrize("raw", [
    '{"label":"deny","label":"allow","confidence":0.9}',
    '{"label":"allow","confidence":0.1,"confidence":0.9}',
    "[" * 1500 + "]" * 1500,
    '\ud800',
])
def test_malformed_or_ambiguous_text_has_bounded_contract_error_and_invalid_benchmark_case(raw):
    with pytest.raises(SpecialistContractError):
        GATE.parse_output(raw)
    predictions = predictions_from_outputs(GATE, [("synthetic-case", "allow", raw)])
    metrics = classification_metrics(GATE, predictions)
    assert metrics["cases"] == 1
    assert metrics["accuracy"] == 0.0
    assert metrics["invalid_output_rate"] == 1.0


def test_extreme_integer_confidence_is_rejected_without_float_overflow():
    with pytest.raises(SpecialistContractError, match="specialist_confidence_invalid"):
        GATE.parse_output({"label": "allow", "confidence": 10 ** 500})
