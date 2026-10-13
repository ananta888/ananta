"""Actual-model gate keeps reply counts without requiring invented unique prose."""

import pytest

from tests.meet_companion_real_model_observation import expected_unique_replies, real_model_scope


@pytest.mark.parametrize("unique", [1, 2, 3])
def test_actual_model_may_reply_with_the_same_valid_text(unique):
    assert expected_unique_replies({"unique": unique}, actual=True) == unique


@pytest.mark.parametrize("unique", [0, 4, True, "3", None])
def test_actual_model_unique_receipt_is_bounded_and_typed(unique):
    with pytest.raises(AssertionError):
        expected_unique_replies({"unique": unique}, actual=True)


def test_synthetic_gate_preserves_three_unique_answers():
    assert expected_unique_replies({"unique": 1}, actual=False) == 3


@pytest.mark.parametrize("assigned,scenario", [(False, "answers-cancel"), (True, "model-cancel")])
def test_actual_model_flag_cannot_silently_select_an_unrelated_scenario(monkeypatch, assigned, scenario):
    monkeypatch.setenv("MEET_COMPANION_INTEGRATED_REAL_LLM", "1")
    with pytest.raises(pytest.skip.Exception):
        real_model_scope(assigned, scenario)


def test_actual_model_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("MEET_COMPANION_INTEGRATED_REAL_LLM", raising=False)
    assert not real_model_scope(True, "answers-cancel")


def test_actual_long_gpu_withdrawal_needs_its_separate_closed_opt_in(monkeypatch):
    monkeypatch.setenv("MEET_COMPANION_INTEGRATED_REAL_LLM", "1")
    monkeypatch.delenv("MEET_COMPANION_INTEGRATED_GPU_WITHDRAWAL", raising=False)
    with pytest.raises(pytest.skip.Exception):
        real_model_scope(True, "speech-mid-speech")
    monkeypatch.setenv("MEET_COMPANION_INTEGRATED_GPU_WITHDRAWAL", "1")
    assert real_model_scope(True, "speech-mid-speech")
    with pytest.raises(pytest.skip.Exception):
        real_model_scope(False, "speech-mid-speech")
    with pytest.raises(pytest.skip.Exception):
        real_model_scope(True, "speech-preparation-paused")
