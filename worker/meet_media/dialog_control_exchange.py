"""Single-flight Hub control read, polled only by its assigned browser runtime."""

import time
from concurrent.futures import ThreadPoolExecutor

from worker.meet_media.dialog_control_retry import ControlReadRetry
from worker.meet_media.dialog_control_transport import ControlReadUnavailable


class DialogControlExchange:
    def __init__(self, hub, meet_session, *, clock=time.monotonic, pool=None):
        self.hub, self.meet_session, self.clock = hub, meet_session, clock
        self.pool = pool if pool is not None else ThreadPoolExecutor(max_workers=1, thread_name_prefix="meet-hub-state")
        self.pending = None
        self.started = 0
        self.obsolete = False
        self.next_request = 0
        self.fresh_until = None
        self.refresh_marker = None
        self.closed = False
        self.retry = ControlReadRetry()

    def refresh(self):
        if self.closed:
            raise ValueError("meet_dialog_control_closed")
        self.obsolete = self.pending is not None
        self.next_request = 0

    def require_fresh(self):
        """Pure checkpoint during bounded renewal; never refresh authority from a timer."""
        if self.closed:
            raise ValueError("meet_dialog_control_closed")
        if self.fresh_until is None or self.clock() >= self.fresh_until:
            raise ValueError("meet_dialog_control_state_stale")

    def poll(self, *, refresh_marker=None):
        if self.closed:
            raise ValueError("meet_dialog_control_closed")
        now = self.clock()
        if self.fresh_until is not None and now >= self.fresh_until:
            raise ValueError("meet_dialog_control_state_stale")
        self.retry.require_window(now)
        if refresh_marker is not None and refresh_marker != self.refresh_marker:
            self.refresh_marker = refresh_marker
            self.refresh()
        if self.pending is not None:
            if now - self.started >= 2.5:
                raise ValueError("meet_dialog_control_request_stale")
            if not self.pending.done():
                return None
            # Even an obsolete policy/protocol denial is terminal. Only the
            # client's closed transient read signal has a bounded recovery path.
            try:
                result = self.pending.result()
            except ControlReadUnavailable:
                self.pending = None
                self.retry.reserve(now=now, started=self.started, fresh_until=self.fresh_until)
                return None
            self.pending = None
            if not self.obsolete:
                self.retry.reset()
                self.fresh_until = now + 2.5
                # Anchor cadence to request start: response latency must not be
                # added again to the next refresh interval.
                self.next_request = self.started + 1
                return result
            if self.retry.deadline is not None:
                # A valid but obsolete reply is not recovery. Its replacement
                # consumes the same finite read budget and cannot skip backoff.
                self.retry.reserve(now=now, started=self.started, fresh_until=self.fresh_until)
                return None
        if now >= max(self.next_request, self.retry.not_before):
            self.started, self.obsolete = now, False
            self.pending = self.pool.submit(self.hub.call, "exchange", meet_session_id=self.meet_session)
        return None

    def close(self):
        self.closed = True
        if self.pending is not None:
            self.pending.cancel()
            self.pending = None
        # Fixed Hub HTTP deadline bounds an already-running request. No response
        # can be applied after close, and no browser object crosses the thread.
        self.pool.shutdown(wait=False, cancel_futures=True)
