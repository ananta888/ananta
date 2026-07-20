"""Hub policy for admitting bounded offline speech-reconciliation work.

The policy is deliberately pure.  Runtime probes are supplied by the Hub so
the worker cannot decide when background work may compete with live media.
"""

from __future__ import annotations

from dataclasses import dataclass

RESOURCE_MODES = frozenset({"immediate", "idle_only", "charging_only", "scheduled", "disabled"})


class SpeechReconciliationResourcePolicyError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SpeechReconciliationResourceRequest:
    mode: str
    requested_factor: int
    user_max_factor: int
    live_call_active: bool
    foreground_load_micros: int
    charging: bool
    minute_of_day: int
    schedule_start_minute: int | None = None
    schedule_end_minute: int | None = None
    quiet_hours: bool = False


@dataclass(frozen=True, slots=True)
class SpeechReconciliationResourceDecision:
    allowed: bool
    action: str
    effective_factor: int
    reason_code: str


class SpeechReconciliationResourcePolicy:
    """Reduce user intent and current pressure to one fail-closed decision."""

    MAX_FOREGROUND_LOAD_MICROS = 700_000
    NORMAL_MAX_FACTOR = 20

    def evaluate(
        self,
        request: SpeechReconciliationResourceRequest,
    ) -> SpeechReconciliationResourceDecision:
        self._validate(request)
        factor = min(request.requested_factor, request.user_max_factor, self.NORMAL_MAX_FACTOR)
        if request.mode == "disabled":
            return self._pause("speech_reconciliation_disabled")
        if request.live_call_active:
            return self._pause("speech_reconciliation_live_pressure")
        if request.quiet_hours:
            return self._pause("speech_reconciliation_quiet_hours")
        if request.foreground_load_micros > self.MAX_FOREGROUND_LOAD_MICROS:
            return self._pause("speech_reconciliation_foreground_pressure")
        if request.mode == "charging_only" and not request.charging:
            return self._pause("speech_reconciliation_not_charging")
        if request.mode == "scheduled" and not self._inside_schedule(request):
            return self._pause("speech_reconciliation_outside_schedule")
        return SpeechReconciliationResourceDecision(
            allowed=True,
            action="run",
            effective_factor=factor,
            reason_code="speech_reconciliation_resource_admitted",
        )

    @staticmethod
    def _pause(reason_code: str) -> SpeechReconciliationResourceDecision:
        return SpeechReconciliationResourceDecision(False, "pause", 0, reason_code)

    @staticmethod
    def _inside_schedule(request: SpeechReconciliationResourceRequest) -> bool:
        assert request.schedule_start_minute is not None
        assert request.schedule_end_minute is not None
        start = request.schedule_start_minute
        end = request.schedule_end_minute
        current = request.minute_of_day
        if start == end:
            return False
        if start < end:
            return start <= current < end
        return current >= start or current < end

    @staticmethod
    def _validate(request: SpeechReconciliationResourceRequest) -> None:
        if request.mode not in RESOURCE_MODES:
            raise SpeechReconciliationResourcePolicyError("speech_reconciliation_resource_mode_invalid")
        for name, value, minimum, maximum in (
            ("requested_factor", request.requested_factor, 1, 20),
            ("user_max_factor", request.user_max_factor, 1, 20),
            ("foreground_load_micros", request.foreground_load_micros, 0, 1_000_000),
            ("minute_of_day", request.minute_of_day, 0, 1439),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise SpeechReconciliationResourcePolicyError(
                    f"speech_reconciliation_{name}_invalid"
                )
        schedule = (request.schedule_start_minute, request.schedule_end_minute)
        if request.mode == "scheduled":
            if any(
                isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1439
                for value in schedule
            ):
                raise SpeechReconciliationResourcePolicyError(
                    "speech_reconciliation_schedule_invalid"
                )
        elif any(value is not None for value in schedule):
            raise SpeechReconciliationResourcePolicyError(
                "speech_reconciliation_schedule_forbidden"
            )


__all__ = [
    "RESOURCE_MODES",
    "SpeechReconciliationResourceDecision",
    "SpeechReconciliationResourcePolicy",
    "SpeechReconciliationResourcePolicyError",
    "SpeechReconciliationResourceRequest",
]
