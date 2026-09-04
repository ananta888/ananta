from hypothesis import find
from hypothesis import strategies as st

from worker.verification.pilot_targets import intentionally_wrong_abs


def test_hypothesis_minimizes_seeded_defect_to_concrete_value() -> None:
    counterexample = find(st.integers(), lambda value: intentionally_wrong_abs(value) < 0)
    assert counterexample == -1
    assert intentionally_wrong_abs(counterexample) < 0


def test_saved_counterexample_reproduces_without_generation() -> None:
    saved_counterexample = -1
    assert intentionally_wrong_abs(saved_counterexample) < 0
