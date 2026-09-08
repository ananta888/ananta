"""Optional terminal I/O owns a deadline only in the already isolated child."""

import math
import signal
import threading
import time


class _ReportExpired(BaseException):
    """Interrupt even an adapter's broad Exception fallback, then suppress here."""


def bounded_terminal_report(send, assignment_deadline):
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "setitimer")
        or type(assignment_deadline) not in {int, float}
        or not math.isfinite(assignment_deadline)
    ):
        return False
    budget = min(1.0, assignment_deadline + 5 - time.monotonic())
    if budget <= 0 or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        return False  # An existing timer has an owner; diagnostics cannot replace it.
    previous = signal.getsignal(signal.SIGALRM)

    def expire(_signum, _frame):
        raise _ReportExpired()

    try:
        signal.signal(signal.SIGALRM, expire)
        signal.setitimer(signal.ITIMER_REAL, budget)
        return send() is True
    except (_ReportExpired, Exception):
        return False
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
