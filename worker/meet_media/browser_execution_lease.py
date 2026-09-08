"""Thread-safe bounded execution freshness; only authenticated Hub updates refresh it."""

import json
import math
import threading
import time

from ananta_contracts.meet_browser_workspace import require_browser_assignment, validate_browser_source


def current_browser_source(projection, assignment, receipt, control, *, now_ms):
    source = validate_browser_source(projection)
    if source["job"] is None:
        return source
    job = require_browser_assignment(source["job"], assignment)
    expected = {
        "meet_session_id": receipt["lease"]["sessionId"],
        "own_peer_id": receipt["peerId"],
        "generation": receipt["lease"]["generation"],
        "membership_epoch": receipt["membershipEpoch"],
        "screen_revision": control["revision"],
        "deadline_ms": min(job["deadline_ms"], assignment["deadline"] * 1000, receipt["lease"]["expiresAt"]),
    }
    if (
        receipt["roomId"] != assignment["meeting"]["room_id"]
        or source["binding"] != expected
        or now_ms >= expected["deadline_ms"]
    ):
        raise ValueError("meet_browser_current_binding_denied")
    return source


def browser_execution_key(source):
    # Screen pause/presentation does not grant or revoke navigation permission.
    # Keep the admitted page alive, but bind every publication separately.
    return json.dumps(
        {
            "job": source["job"],
            "binding": {key: value for key, value in source["binding"].items() if key != "screen_revision"},
        },
        sort_keys=True,
        separators=(",", ":"),
    )


class BrowserExecutionLease:
    def __init__(self, source, *, clock=time.monotonic, wall_clock=time.time):
        self.clock, self.lock, self.closed = clock, threading.Lock(), False
        self.key = browser_execution_key(source)
        remaining = source["binding"]["deadline_ms"] / 1000 - wall_clock()
        if not math.isfinite(remaining) or not 0 < remaining <= 30:
            raise ValueError("meet_browser_lease_expired")
        now = clock()
        self.deadline, self.fresh_until = now + remaining, now + 2.5

    def refresh(self, source):
        with self.lock:
            self._require()
            if browser_execution_key(source) != self.key:
                self.closed = True
                raise ValueError("meet_browser_lease_changed")
            self.fresh_until = self.clock() + 2.5

    def _require(self):
        now = self.clock()
        if self.closed or not math.isfinite(now) or now >= min(self.deadline, self.fresh_until):
            self.closed = True
            raise ValueError("meet_browser_lease_expired")

    def require(self):
        with self.lock:
            self._require()

    def close(self):
        with self.lock:
            self.closed = True
