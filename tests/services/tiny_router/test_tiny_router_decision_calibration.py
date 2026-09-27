"""Threshold calibration for decision-based tool choice: precision, coverage, a provable recommendation."""

import pytest

from agent.services.tiny_router.decision_calibration import (
    Observation,
    calibration_report,
    evaluate_at,
    expected_calibration_error,
    is_holdout,
    recommend_threshold,
    split_report,
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


def test_a_defensible_alternative_counts_as_correct():
    assert Observation("x", "search", "overview", 0.6, ("overview",)).correct
    assert not Observation("x", "search", "overview", 0.6).correct


def test_the_holdout_split_is_stable_and_about_a_fifth():
    ids = [f"case-{index:03d}" for index in range(500)]
    held = [case_id for case_id in ids if is_holdout(case_id)]
    assert held == [case_id for case_id in ids if is_holdout(case_id)]
    assert 70 <= len(held) <= 130


def test_the_threshold_is_chosen_on_validation_and_only_measured_on_holdout():
    rows = [Observation(f"ok-{index}", "a", "a", 0.97) for index in range(300)]
    rows += [Observation(f"bad-{index}", "a", "b", 0.7) for index in range(40)]
    report = split_report(rows, target_precision=0.95)
    assert report["recommended_on_validation"] == 0.75
    holdout = [item for item in rows if is_holdout(item.case_id)]
    assert report["holdout"] == evaluate_at(holdout, 0.75)
    assert report["holdout"]["precision"] == 1.0 and report["holdout"]["total"] == len(holdout)
    assert report["validation"]["total"] + len(holdout) == len(rows)


def test_without_a_recommendation_the_fallback_threshold_is_measured():
    report = split_report([Observation(f"c{index}", "a", "a", 0.9) for index in range(20)], fallback_threshold=0.8)
    assert report["recommended_on_validation"] is None and report["applied_threshold"] == 0.8
