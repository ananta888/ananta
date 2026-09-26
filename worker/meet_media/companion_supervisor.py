"""Keep the companion in its room across session losses.

A companion session ends whenever the room server denies a lease renewal,
the page drops the membership, or the Hub is briefly unreachable. Each new
attempt fetches a fresh Hub grant, so the Hub keeps authorizing every rejoin;
this loop only decides *when* to try again. It stops for good once the stop
file exists, so an operator can still end the companion deliberately.
"""

from dataclasses import dataclass
from typing import Callable

MOVED = "moved"


@dataclass(frozen=True)
class RejoinBackoff:
    """Exponential delay between failed sessions, reset after a stable one."""

    initial: float = 5.0
    maximum: float = 300.0
    stable_after: float = 600.0

    def next_delay(self, previous: float, session_seconds: float) -> float:
        if session_seconds >= self.stable_after or previous <= 0:
            return self.initial
        return min(previous * 2, self.maximum)


def supervise(
    run: Callable[[], object],
    stop_requested: Callable[[], bool],
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    log: Callable[[str], None],
    backoff: RejoinBackoff = RejoinBackoff(),
) -> None:
    """Run sessions until the stop file appears; never exit on a lost session."""
    delay = 0.0
    while not stop_requested():
        started = clock()
        try:
            outcome = run()
        except Exception as error:  # noqa: BLE001 - any session failure is retried
            outcome = "error"
            log("session_err %r" % (error,))
        if stop_requested():
            break
        if outcome == MOVED:
            log("rejoin after room move")
            delay = 0.0
            continue
        delay = backoff.next_delay(delay, clock() - started)
        log("rejoin in %.0fs after %s" % (delay, outcome or "left"))
        _sleep_unless_stopped(delay, stop_requested, sleep)


def _sleep_unless_stopped(seconds: float, stop_requested: Callable[[], bool], sleep: Callable[[float], None]) -> None:
    remaining = seconds
    while remaining > 0 and not stop_requested():
        step = min(1.0, remaining)
        sleep(step)
        remaining -= step
