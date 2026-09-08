"""One optional terminal measurement, never a timer, scheduler or authority."""

import math
import time
from dataclasses import dataclass

from ananta_contracts.meet_dialog_diagnostics import validate_observation


@dataclass(frozen=True)
class ProcessUsage:
    self_cpu: float
    children_cpu: float
    self_rss: int
    child_rss: int


def process_usage():
    import resource

    own, children = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return ProcessUsage(
        own.ru_utime + own.ru_stime, children.ru_utime + children.ru_stime, own.ru_maxrss, children.ru_maxrss
    )


def stop_reason(completed, failure):
    if completed:
        return "assignment_elapsed"
    # No exception formatting, traceback, arbitrary object attribute or message
    # goes into the report. Only exact built-in, fixed known errors are mapped.
    code = (
        failure.args[0]
        if type(failure) is ValueError and len(failure.args) == 1 and type(failure.args[0]) is str
        else None
    )
    if code in {"meet_dialog_control_state_stale", "meet_dialog_control_request_stale"}:
        return "control_stale"
    if code == "meet_dialog_hub_revoked_or_unavailable":
        return "hub_unavailable_or_revoked"
    if code in {
        "meet_dialog_session_expired",
        "meet_dialog_session_operation_failed",
        "meet_machine_navigation_denied",
    }:
        return "session_failed"
    return "runtime_failed"


class DialogRunDiagnostics:
    def __init__(self, *, enabled=False, clock=time.monotonic, usage=process_usage):
        self.enabled, self.clock, self.usage = enabled is True, clock, usage
        self.reported = False
        self.baseline = None
        if self.enabled:
            try:
                self.started, self.baseline = self.clock(), self.usage()
            except Exception:
                pass  # Missing measurements stay explicitly missing.

    def _measure(self):
        if self.baseline is None:
            return None
        try:
            end, elapsed = self.usage(), self.clock() - self.started
            self_cpu, children_cpu = (
                end.self_cpu - self.baseline.self_cpu,
                end.children_cpu - self.baseline.children_cpu,
            )
            if any(not math.isfinite(value) or value < 0 for value in (elapsed, self_cpu, children_cpu)):
                return None
            measurements = {
                "elapsed_ms": int(elapsed * 1000),
                "self_cpu_ms": int(self_cpu * 1000),
                "terminated_children_cpu_ms": int(children_cpu * 1000),
                "self_max_rss_kib": end.self_rss,
                "terminated_child_max_rss_kib": end.child_rss,
            }
            return validate_observation(
                {
                    "schema": "ananta.meet-dialog-terminal-observation.v1",
                    "stop_reason": "runtime_failed",
                    "measurements": measurements,
                }
            )["measurements"]
        except Exception:
            return None

    def finish(self, send, *, completed=False, failure=None):
        if not self.enabled or self.reported:
            return False
        self.reported = True  # Uncertain delivery never causes another attempt.
        try:
            observation = validate_observation(
                {
                    "schema": "ananta.meet-dialog-terminal-observation.v1",
                    "stop_reason": stop_reason(completed, failure),
                    "measurements": self._measure(),
                }
            )
            return send(observation) is True
        except Exception:
            return False  # Reporting cannot replace the original run outcome.
