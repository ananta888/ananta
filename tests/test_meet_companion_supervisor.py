"""Companion rejoin loop: sessions, clock and sleep are deterministic doubles."""

import pytest

from worker.meet_media.companion_supervisor import MOVED, RejoinBackoff, supervise

pytestmark = pytest.mark.timeout(30)


class Harness:
    def __init__(self, outcomes, session_seconds=1.0):
        self.outcomes = list(outcomes)
        self.session_seconds = session_seconds
        self.now = 0.0
        self.runs = 0
        self.slept = []
        self.lines = []
        self.stop = False

    def run(self):
        self.runs += 1
        self.now += self.session_seconds
        outcome = self.outcomes.pop(0)
        if not self.outcomes:
            self.stop = True
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def supervise(self, **kwargs):
        supervise(
            self.run, lambda: self.stop, clock=lambda: self.now, sleep=self.sleep, log=self.lines.append, **kwargs
        )


def test_lost_session_rejoins_after_a_delay_instead_of_exiting():
    harness = Harness([None, None])
    harness.supervise()
    assert harness.runs == 2
    assert sum(harness.slept) == 5.0
    assert "rejoin in 5s after left" in harness.lines


def test_session_exception_is_logged_and_retried():
    harness = Harness([RuntimeError("hub unreachable"), None])
    harness.supervise()
    assert harness.runs == 2
    assert any(line.startswith("session_err RuntimeError('hub unreachable')") for line in harness.lines)
    assert "rejoin in 5s after error" in harness.lines


def test_room_move_rejoins_immediately():
    harness = Harness([MOVED, None])
    harness.supervise()
    assert harness.runs == 2
    assert harness.slept == []
    assert "rejoin after room move" in harness.lines


def test_repeated_short_sessions_back_off_exponentially_up_to_the_cap():
    harness = Harness([None] * 6)
    harness.supervise(backoff=RejoinBackoff(initial=5, maximum=20, stable_after=600))
    delays = [float(line.split()[2].rstrip("s")) for line in harness.lines if line.startswith("rejoin in")]
    assert delays == [5, 10, 20, 20, 20]


def test_stable_session_resets_the_backoff():
    backoff = RejoinBackoff(initial=5, maximum=300, stable_after=600)
    assert backoff.next_delay(160, session_seconds=599) == 300
    assert backoff.next_delay(160, session_seconds=600) == 5


def test_stop_file_ends_the_loop_without_another_session():
    harness = Harness([None])
    harness.supervise()
    assert harness.runs == 1
    assert harness.slept == []


def test_stop_during_the_backoff_wait_cancels_the_rejoin():
    harness = Harness([None, None])

    def sleep(seconds):
        harness.slept.append(seconds)
        harness.stop = True

    supervise(harness.run, lambda: harness.stop, clock=lambda: harness.now, sleep=sleep, log=harness.lines.append)
    assert harness.runs == 1
    assert harness.slept == [1.0]
