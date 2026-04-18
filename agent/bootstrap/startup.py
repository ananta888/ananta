import logging
import time
from collections.abc import Callable
from typing import TypeVar

from agent.metrics import APP_STARTUP_FAILURES_TOTAL, APP_STARTUP_PHASE_DURATION, APP_STARTUP_PHASE_TOTAL

T = TypeVar("T")


def run_startup_phase(phase: str, action: Callable[..., T], *args, **kwargs) -> T:
    started = time.perf_counter()
    try:
        result = action(*args, **kwargs)
    except Exception as exc:
        elapsed = time.perf_counter() - started
        _record_phase(phase, elapsed, "error")
        APP_STARTUP_FAILURES_TOTAL.labels(phase=phase, error_type=type(exc).__name__).inc()
        logging.exception(
            "Startup phase failed: phase=%s duration=%.4fs error_type=%s",
            phase,
            elapsed,
            type(exc).__name__,
        )
        raise

    elapsed = time.perf_counter() - started
    _record_phase(phase, elapsed, "success")
    logging.info("Startup phase completed: phase=%s duration=%.4fs", phase, elapsed)
    return result


def _record_phase(phase: str, elapsed: float, status: str) -> None:
    APP_STARTUP_PHASE_DURATION.labels(phase=phase, status=status).observe(elapsed)
    APP_STARTUP_PHASE_TOTAL.labels(phase=phase, status=status).inc()
