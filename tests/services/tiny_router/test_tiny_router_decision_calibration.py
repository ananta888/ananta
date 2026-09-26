"""Threshold calibration for decision-based tool choice: precision, coverage, a provable recommendation."""

import pytest

from agent.services.tiny_router.decision_calibration import (
    Observation,
    calibration_report,
    expected_calibration_error,
    recommend_threshold,
    threshold_table,
    wilson_lower_bound,
)

pytestmark = pytest.mark.timeout(30)


def observations(correct_high=0, wrong_low=0, wrong_high=0, correct_low=0):
    rows = []
    for index in range(correct_high):
        rows.append(Observation(f"ch{index}", "a", "a", 0.98))
    for index in range(wrong_high):
        rows.append(Observation(f"wh{index}", "a", "b", 0.96))
    for index in range(wrong_low):
        rows.append(Observation(f"wl{index}", "a", "b", 0.6))
    for index in range(correct_low):
        rows.append(Observation(f"cl{index}", "a", "a", 0.62))
    return rows


def test_the_table_splits_precision_and_coverage_by_threshold():
    rows = {row.threshold: row for row in threshold_table(observations(8, wrong_low=2))}
    assert (rows[0.5].accepted, rows[0.5].precision, rows[0.5].coverage) == (10, 0.8, 1.0)
    assert (rows[0.7].accepted, rows[0.7].precision, rows[0.7].coverage) == (8, 1.0, 0.8)


def test_a_small_error_free_sample_is_no_proof():
    assert wilson_lower_bound(48, 48) < 0.95
    assert recommend_threshold(threshold_table(observations(48)), target_precision=0.95) is None


def test_enough_evidence_recommends_the_lowest_safe_threshold():
    rows = threshold_table(observations(200, wrong_low=30, correct_low=10))
    assert recommend_threshold(rows, target_precision=0.95) == 0.65


def test_errors_above_the_correct_answers_block_a_recommendation():
    confident_errors = [Observation(f"e{index}", "a", "b", 0.995) for index in range(20)]
    rows = threshold_table(observations(100) + confident_errors)
    assert recommend_threshold(rows, target_precision=0.95) is None


def test_errors_below_a_threshold_band_are_cut_off():
    rows = threshold_table(observations(300, wrong_high=20))
    assert recommend_threshold(rows, target_precision=0.95) == 0.97


def test_the_report_names_errors_and_calibration_gap():
    report = calibration_report(observations(8, wrong_low=2))
    assert report["accuracy"] == 0.8 and len(report["errors"]) == 2
    assert report["confusion"] == {"a": {"a": 8, "b": 2}}
    assert expected_calibration_error([Observation("x", "a", "a", 1.0)]) == 0.0
