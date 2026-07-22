"""Deterministic Hub policy for SFU node health and admission readiness."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum


class SfuNodeHealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SfuNodeHealthDimension:
    status: SfuNodeHealthStatus
    reason_code: str


@dataclass(frozen=True, slots=True)
class SfuNodeHealthPolicy:
    observation_ttl_seconds: float = 30.0
    probe_deadline_seconds: float = 2.0
    failure_threshold: int = 3
    success_threshold: int = 2
    flap_cooldown_seconds: float = 30.0
    clock_skew_seconds: float = 5.0

    def __post_init__(self) -> None:
        for value in (
            self.observation_ttl_seconds,
            self.probe_deadline_seconds,
            self.flap_cooldown_seconds,
            self.clock_skew_seconds,
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError("sfu_health_duration_invalid")
        if self.observation_ttl_seconds <= 0 or self.probe_deadline_seconds <= 0:
            raise ValueError("sfu_health_duration_invalid")
        for value in (self.failure_threshold, self.success_threshold):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("sfu_health_threshold_invalid")


@dataclass(frozen=True, slots=True)
class SfuNodeHealthObservation:
    observation_id: str
    boot_id: str
    measured_at: float
    expires_at: float
    signature_verified: bool
    revoked: bool
    compatible: bool
    drain_state: str
    self_reported_health: str
    liveness: bool | None
    control_ready: bool | None
    media_ready: bool | None
    admission_ready: bool | None


@dataclass(frozen=True, slots=True)
class SfuNodeActiveProbe:
    started_at: float
    completed_at: float | None
    liveness: bool | None
    control_ready: bool | None
    media_ready: bool | None
    admission_ready: bool | None


@dataclass(frozen=True, slots=True)
class SfuNodeHealthHistory:
    boot_id: str | None = None
    consecutive_successes: int = 0
    consecutive_failures: int = 0
    stable_status: SfuNodeHealthStatus = SfuNodeHealthStatus.UNKNOWN
    last_transition_at: float | None = None
    flap_cooldown_until: float = 0.0


@dataclass(frozen=True, slots=True)
class SfuNodeHealthEvaluation:
    liveness: SfuNodeHealthDimension
    control_readiness: SfuNodeHealthDimension
    media_readiness: SfuNodeHealthDimension
    admission_readiness: SfuNodeHealthDimension
    drain: SfuNodeHealthDimension
    overall: SfuNodeHealthStatus
    admission_allowed: bool
    reason_codes: tuple[str, ...]
    evaluated_at: float
    history: SfuNodeHealthHistory


class SfuNodeHealthEvaluator:
    """Reduce authenticated observations and active probes into Hub policy state."""

    _DRAINING_STATES = frozenset(
        {"requested", "admission_stopped", "draining", "drained", "forced"}
    )

    def __init__(self, policy: SfuNodeHealthPolicy | None = None, *, clock=time.time) -> None:
        self._policy = policy or SfuNodeHealthPolicy()
        self._clock = clock

    def evaluate(
        self,
        observation: SfuNodeHealthObservation | None,
        probe: SfuNodeActiveProbe | None,
        previous: SfuNodeHealthHistory | None = None,
    ) -> SfuNodeHealthEvaluation:
        now = float(self._clock())
        history = previous or SfuNodeHealthHistory()
        if observation is None:
            return self._terminal_unknown(now, history, "sfu_health_observation_missing")
        if not observation.signature_verified:
            return self._terminal_unknown(now, history, "sfu_health_observation_unverified")
        if observation.measured_at > now + self._policy.clock_skew_seconds:
            return self._terminal_unknown(now, history, "sfu_health_clock_skew")
        fresh_until = min(
            observation.expires_at,
            observation.measured_at + self._policy.observation_ttl_seconds,
        )
        if now >= fresh_until:
            return self._terminal_unknown(now, history, "sfu_health_observation_stale")
        if observation.revoked:
            return self._terminal_unhealthy(now, history, observation.boot_id, "sfu_health_node_revoked")
        if not observation.compatible:
            return self._terminal_unhealthy(
                now, history, observation.boot_id, "sfu_health_version_incompatible"
            )

        restarted = history.boot_id not in {None, observation.boot_id}
        if restarted:
            history = SfuNodeHealthHistory(boot_id=observation.boot_id)

        probe_reason = self._probe_invalid_reason(probe, now)
        liveness = self._dimension(
            "liveness", observation.liveness, None if probe is None else probe.liveness, probe_reason
        )
        control = self._dimension(
            "control", observation.control_ready, None if probe is None else probe.control_ready, probe_reason
        )
        media = self._dimension(
            "media", observation.media_ready, None if probe is None else probe.media_ready, probe_reason
        )
        admission = self._dimension(
            "admission",
            observation.admission_ready,
            None if probe is None else probe.admission_ready,
            probe_reason,
        )
        drain = self._drain_dimension(observation.drain_state)
        dimensions = (liveness, control, media, admission, drain)
        reasons = [item.reason_code for item in dimensions if item.status is not SfuNodeHealthStatus.HEALTHY]

        if restarted:
            reasons.append("sfu_health_restart_recovery_required")
        if observation.self_reported_health == "healthy" and any(
            item.status is not SfuNodeHealthStatus.HEALTHY for item in dimensions
        ):
            reasons.append("sfu_health_self_report_contradicted")
        if observation.self_reported_health not in {"healthy", "degraded", "unhealthy"}:
            reasons.append("sfu_health_self_report_unknown")

        has_unknown = any(item.status is SfuNodeHealthStatus.UNKNOWN for item in dimensions)
        has_failure = any(item.status is SfuNodeHealthStatus.UNHEALTHY for item in dimensions)
        self_report_blocks = observation.self_reported_health != "healthy"
        immediate_safety_block = (
            drain.status is SfuNodeHealthStatus.UNHEALTHY or probe_reason is not None
        )

        successes = history.consecutive_successes + 1 if not has_unknown and not has_failure and not self_report_blocks else 0
        failures = history.consecutive_failures + 1 if has_failure or self_report_blocks else 0
        stable = history.stable_status
        transition_at = history.last_transition_at
        cooldown_until = history.flap_cooldown_until

        if has_unknown:
            overall = SfuNodeHealthStatus.UNKNOWN
        elif has_failure or self_report_blocks:
            if immediate_safety_block or failures >= self._policy.failure_threshold:
                overall = SfuNodeHealthStatus.UNHEALTHY
                if stable is SfuNodeHealthStatus.HEALTHY:
                    cooldown_until = max(cooldown_until, now + self._policy.flap_cooldown_seconds)
                    transition_at = now
                stable = SfuNodeHealthStatus.UNHEALTHY
            else:
                overall = SfuNodeHealthStatus.DEGRADED
                reasons.append("sfu_health_failure_threshold_pending")
        elif successes < self._policy.success_threshold:
            overall = SfuNodeHealthStatus.UNKNOWN
            reasons.append("sfu_health_success_threshold_pending")
        elif now < cooldown_until:
            overall = SfuNodeHealthStatus.DEGRADED
            reasons.append("sfu_health_flap_cooldown")
        else:
            overall = SfuNodeHealthStatus.HEALTHY
            if stable is not SfuNodeHealthStatus.HEALTHY:
                transition_at = now
            stable = SfuNodeHealthStatus.HEALTHY

        next_history = SfuNodeHealthHistory(
            boot_id=observation.boot_id,
            consecutive_successes=successes,
            consecutive_failures=failures,
            stable_status=stable,
            last_transition_at=transition_at,
            flap_cooldown_until=cooldown_until,
        )
        admission_allowed = (
            overall is SfuNodeHealthStatus.HEALTHY
            and admission.status is SfuNodeHealthStatus.HEALTHY
            and drain.status is SfuNodeHealthStatus.HEALTHY
        )
        return SfuNodeHealthEvaluation(
            liveness=liveness,
            control_readiness=control,
            media_readiness=media,
            admission_readiness=admission,
            drain=drain,
            overall=overall,
            admission_allowed=admission_allowed,
            reason_codes=tuple(dict.fromkeys(reasons)),
            evaluated_at=now,
            history=next_history,
        )

    def _probe_invalid_reason(self, probe: SfuNodeActiveProbe | None, now: float) -> str | None:
        if probe is None:
            return "sfu_health_probe_missing"
        if probe.started_at > now + self._policy.clock_skew_seconds:
            return "sfu_health_probe_clock_skew"
        if probe.completed_at is None or probe.completed_at < probe.started_at:
            return "sfu_health_probe_timeout"
        if probe.completed_at - probe.started_at > self._policy.probe_deadline_seconds:
            return "sfu_health_probe_timeout"
        return None

    @staticmethod
    def _dimension(
        name: str,
        observed: bool | None,
        probed: bool | None,
        probe_reason: str | None,
    ) -> SfuNodeHealthDimension:
        if probe_reason is not None:
            status = (
                SfuNodeHealthStatus.UNKNOWN
                if probe_reason in {"sfu_health_probe_missing", "sfu_health_probe_clock_skew"}
                else SfuNodeHealthStatus.UNHEALTHY
            )
            return SfuNodeHealthDimension(status, probe_reason)
        if observed is None or probed is None:
            return SfuNodeHealthDimension(
                SfuNodeHealthStatus.UNKNOWN, f"sfu_health_{name}_signal_missing"
            )
        if not observed:
            return SfuNodeHealthDimension(
                SfuNodeHealthStatus.UNHEALTHY, f"sfu_health_{name}_observation_failed"
            )
        if not probed:
            return SfuNodeHealthDimension(
                SfuNodeHealthStatus.UNHEALTHY, f"sfu_health_{name}_probe_failed"
            )
        return SfuNodeHealthDimension(SfuNodeHealthStatus.HEALTHY, f"sfu_health_{name}_ready")

    def _drain_dimension(self, state: str) -> SfuNodeHealthDimension:
        if state in {"active", "cancelled"}:
            return SfuNodeHealthDimension(SfuNodeHealthStatus.HEALTHY, "sfu_health_drain_clear")
        if state in self._DRAINING_STATES:
            return SfuNodeHealthDimension(
                SfuNodeHealthStatus.UNHEALTHY, f"sfu_health_drain_{state}"
            )
        return SfuNodeHealthDimension(SfuNodeHealthStatus.UNKNOWN, "sfu_health_drain_unknown")

    def _terminal_unknown(
        self, now: float, history: SfuNodeHealthHistory, reason: str
    ) -> SfuNodeHealthEvaluation:
        unknown = SfuNodeHealthDimension(SfuNodeHealthStatus.UNKNOWN, reason)
        return SfuNodeHealthEvaluation(
            unknown, unknown, unknown, unknown, unknown,
            SfuNodeHealthStatus.UNKNOWN, False, (reason,), now, history
        )

    def _terminal_unhealthy(
        self,
        now: float,
        history: SfuNodeHealthHistory,
        boot_id: str,
        reason: str,
    ) -> SfuNodeHealthEvaluation:
        unhealthy = SfuNodeHealthDimension(SfuNodeHealthStatus.UNHEALTHY, reason)
        next_history = SfuNodeHealthHistory(
            boot_id=boot_id,
            consecutive_failures=history.consecutive_failures + 1,
            stable_status=SfuNodeHealthStatus.UNHEALTHY,
            last_transition_at=now,
            flap_cooldown_until=max(
                history.flap_cooldown_until, now + self._policy.flap_cooldown_seconds
            ),
        )
        return SfuNodeHealthEvaluation(
            unhealthy, unhealthy, unhealthy, unhealthy, unhealthy,
            SfuNodeHealthStatus.UNHEALTHY, False, (reason,), now, next_history
        )


__all__ = [
    "SfuNodeActiveProbe",
    "SfuNodeHealthDimension",
    "SfuNodeHealthEvaluation",
    "SfuNodeHealthEvaluator",
    "SfuNodeHealthHistory",
    "SfuNodeHealthObservation",
    "SfuNodeHealthPolicy",
    "SfuNodeHealthStatus",
]
