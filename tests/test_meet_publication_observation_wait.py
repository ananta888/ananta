"""Deterministic cadence and deadline checks; no real sleeps or human input."""

import pytest

from tests.meet_publication_observation_wait import wait_for_publications

pytestmark = pytest.mark.timeout(30)


def test_once_per_second_worker_update_has_a_full_bounded_observation_window():
    now = [0.0]

    def sleep(delay):
        now[0] += delay

    def observe():
        return {"publications": ["synthetic"] if now[0] >= 1.1 else [], "publicationRevision": 5}

    receipt, elapsed = wait_for_publications(observe, True, clock=lambda: now[0], sleep=sleep)
    assert receipt["publications"] and 1100 <= elapsed <= 1300


def test_absent_transition_stops_at_three_seconds_with_only_closed_diagnostics():
    now = [0.0]

    def sleep(delay):
        now[0] += delay

    with pytest.raises(AssertionError) as error:
        wait_for_publications(
            lambda: {"publications": [], "publicationRevision": 0, "secret": "PRIVATE"},
            True,
            clock=lambda: now[0],
            sleep=sleep,
        )
    assert now[0] == 3 and "PRIVATE" not in str(error.value)
    assert len(error.value.args[0]["samples"]) <= 8


def test_success_after_an_overlong_network_call_is_not_accepted():
    now = [0.0]

    def observe():
        now[0] = 3.1
        return {"publications": ["synthetic"], "publicationRevision": 1}

    with pytest.raises(AssertionError):
        wait_for_publications(observe, True, clock=lambda: now[0], sleep=lambda _: None)


def test_frozen_clock_has_a_fixed_iteration_cap():
    reads = []

    def observe():
        reads.append(True)
        return {"publications": [], "publicationRevision": 0}

    with pytest.raises(AssertionError):
        wait_for_publications(observe, True, clock=lambda: 0, sleep=lambda _: None)
    assert len(reads) == 32


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf")])
def test_clock_rollback_or_nonfinite_value_cannot_extend_the_budget(bad):
    ticks = iter([0, bad])
    with pytest.raises(ValueError, match="clock_invalid"):
        wait_for_publications(lambda: {}, True, clock=lambda: next(ticks), sleep=lambda _: None)
