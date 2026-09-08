"""Worker-parent resource watchdog, independent of the child's browser event loop."""

import select
import time


def watch_dialog_progress(process, channel, budget, stop, *, clock=time.monotonic):
    try:
        while process.poll() is None:
            budget.require(clock())
            channel.consume(budget, clock)
            budget.require(clock())
            select.select([channel.reader], [], [], min(0.05, max(0, budget.deadline - clock())))
    except Exception:
        stop(process)  # Only this already-owned process group; never redispatch.
