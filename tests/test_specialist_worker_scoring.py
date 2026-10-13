"""Shared scoring checks; synthetic outputs are never production model evidence."""

import json

import pytest

from agent.services.specialist_training_task_family import score_specialist_output as hub_score
from ananta_contracts.lora_evaluation import score_evaluation_output, score_specialist_output
from ananta_contracts.specialist_decision import OUTPUT_SCHEMA
from tests.worker.test_lora_training_contracts import _evaluation_request
from worker.training.contracts import parse_job_request


def output(label="allow", **extra):
    return json.dumps({"schema": OUTPUT_SCHEMA, "label": label, **extra})


@pytest.mark.parametrize("confidence", [0, 1, 0.7])
def test_shared_scorer_accepts_closed_output_and_matches_reference_label(confidence):
    generated = output(confidence=confidence)
    reference = output(confidence=0.5)
    assert hub_score(generated) == score_specialist_output(generated)
    scored = score_evaluation_output("specialist_decision", generated, expected_output=reference)
    assert scored["schema_valid"] and scored["expected_valid"] and scored["correct"]
    assert scored["total"] == 1.0


@pytest.mark.parametrize("confidence", [True, False, None, -0.1, 1.1, "0.5", float("nan"), float("inf")])
def test_invalid_confidence_never_scores_as_valid(confidence):
    generated = output(confidence=confidence)
    assert not hub_score(generated)["schema_valid"]
    assert score_evaluation_output("specialist_decision", generated, expected_output=output())["total"] == 0.0


@pytest.mark.parametrize(
    "generated",
    ["free text", "[]", "null", output(" "), output(shell="execute"), json.dumps({"schema": None, "label": "allow"})],
)
def test_open_or_malformed_outputs_are_rejected(generated):
    assert not score_specialist_output(generated)["schema_valid"]


def test_wrong_label_has_zero_score_even_when_output_shape_is_valid():
    scored = score_evaluation_output("specialist_decision", output("allow"), expected_output=output("deny"))
    assert scored["schema_valid"] and scored["expected_valid"]
    assert not scored["correct"] and scored["total"] == 0.0


def test_malformed_reference_cannot_reward_valid_generated_label():
    scored = score_evaluation_output("specialist_decision", output(), expected_output="untyped label")
    assert scored["schema_valid"] and not scored["expected_valid"]
    assert not scored["correct"] and scored["total"] == 0.0


@pytest.mark.parametrize("invalid", ['{"label":"deny","label":"allow"}', "[" * 2000 + "]" * 2000])
def test_ambiguous_or_excessively_nested_json_is_bounded_invalid_output(invalid):
    assert not score_specialist_output(invalid)["schema_valid"]
    assert score_evaluation_output("specialist_decision", invalid, expected_output=output())["total"] == 0.0
    assert score_evaluation_output("specialist_decision", output(), expected_output=invalid)["total"] == 0.0


def test_worker_contract_admits_shared_specialist_scorer():
    request = _evaluation_request()
    request["configuration"]["scorer_name"] = "specialist_decision"
    assert parse_job_request(request).configuration.scorer_name == "specialist_decision"
