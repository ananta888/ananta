"""Bounded state convergence after one explicit control command, never a retry."""

import math
import time


def wait_for_publications(observe, enabled, *, clock=time.monotonic, sleep=time.sleep):
    if type(enabled) is not bool:
        raise ValueError("test_publication_expected_state_invalid")
    started = clock()
    if not math.isfinite(started):
        raise ValueError("test_publication_clock_invalid")
    previous = started
    samples = []
    # Worker controls refresh once a second. Eight 100-ms sleeps did not cover
    # one complete refresh period; the assertion now uses a strict elapsed budget.
    for _ in range(32):
        before = clock()
        if not math.isfinite(before) or before < previous:
            raise ValueError("test_publication_clock_invalid")
        if before - started >= 3:
            break
        observed = observe()
        now = clock()
        if not math.isfinite(now) or now < before:
            raise ValueError("test_publication_clock_invalid")
        elapsed = now - started
        samples.append(
            {
                "elapsed_ms": round(elapsed * 1000, 2),
                "sources": len(observed["publications"]),
                "revision": observed["publicationRevision"],
            }
        )
        if elapsed >= 3:
            break
        if bool(observed["publications"]) == enabled:
            return observed, round(elapsed * 1000, 2)
        previous = now
        sleep(min(0.1, 3 - elapsed))
    raise AssertionError(
        {"code": "bounded_publication_state_convergence_missing", "enabled": enabled, "samples": samples[-8:]}
    )
