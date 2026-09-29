from dataclasses import replace

from agent.services.sfu_node_health_evaluator import (
    SfuNodeActiveProbe,
    SfuNodeHealthEvaluator,
    SfuNodeHealthObservation,
    SfuNodeHealthPolicy,
    SfuNodeHealthStatus,
)


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def observation(**changes):
    value = SfuNodeHealthObservation(
        observation_id="obs-1",
        boot_id="boot-1",
        measured_at=99.0,
        expires_at=120.0,
        signature_verified=True,
        revoked=False,
        compatible=True,
        drain_state="active",
        self_reported_health="healthy",
        liveness=True,
        control_ready=True,
        media_ready=True,
        admission_ready=True,
    )
    return replace(value, **changes)


def probe(**changes):
    value = SfuNodeActiveProbe(
        started_at=99.0,
        completed_at=99.5,
        liveness=True,
        control_ready=True,
        media_ready=True,
        admission_ready=True,
    )
    return replace(value, **changes)


def test_success_threshold_stale_partial_and_self_report_are_fail_closed():
    clock = Clock()
    evaluator = SfuNodeHealthEvaluator(
        SfuNodeHealthPolicy(success_threshold=2, failure_threshold=2), clock=clock
    )
    first = evaluator.evaluate(observation(), probe())
    assert first.overall is SfuNodeHealthStatus.UNKNOWN
    ready = evaluator.evaluate(observation(), probe(), first.history)
    assert ready.overall is SfuNodeHealthStatus.HEALTHY
    assert ready.admission_allowed is True

    partial = evaluator.evaluate(
        observation(media_ready=None), probe(), ready.history
    )
    assert partial.overall is SfuNodeHealthStatus.UNKNOWN
    assert partial.media_readiness.reason_code == "sfu_health_media_signal_missing"

    contradicted = evaluator.evaluate(
        observation(control_ready=False), probe(), ready.history
    )
    assert contradicted.admission_allowed is False
    assert "sfu_health_self_report_contradicted" in contradicted.reason_codes

    clock.now = 121.0
    stale = evaluator.evaluate(observation(), probe(), ready.history)
    assert stale.overall is SfuNodeHealthStatus.UNKNOWN
    assert stale.reason_codes == ("sfu_health_observation_stale",)


def test_network_partition_flapping_restart_and_recovery_use_fake_clock():
    clock = Clock()
    policy = SfuNodeHealthPolicy(
        success_threshold=1,
        failure_threshold=2,
        flap_cooldown_seconds=10,
        probe_deadline_seconds=1,
    )
    evaluator = SfuNodeHealthEvaluator(policy, clock=clock)
    healthy = evaluator.evaluate(observation(), probe())
    assert healthy.admission_allowed

    first_failure = evaluator.evaluate(
        observation(), probe(completed_at=101.0), healthy.history
    )
    assert first_failure.admission_allowed is False
    assert first_failure.overall is SfuNodeHealthStatus.UNHEALTHY
    assert first_failure.history.flap_cooldown_until == 110.0

    recovered_during_cooldown = evaluator.evaluate(
        observation(), probe(), first_failure.history
    )
    assert recovered_during_cooldown.overall is SfuNodeHealthStatus.DEGRADED
    clock.now = 111.0
    recovered = evaluator.evaluate(
        replace(observation(), measured_at=110.0, expires_at=130.0),
        replace(probe(), started_at=110.0, completed_at=110.5),
        recovered_during_cooldown.history,
    )
    assert recovered.admission_allowed

    restarted = evaluator.evaluate(
        replace(observation(), boot_id="boot-2", measured_at=110.0, expires_at=130.0),
        replace(probe(), started_at=110.0, completed_at=110.5),
        recovered.history,
    )
    assert "sfu_health_restart_recovery_required" in restarted.reason_codes


def test_drain_revoke_incompatibility_and_unknown_never_admit():
    evaluator = SfuNodeHealthEvaluator(
        SfuNodeHealthPolicy(success_threshold=1), clock=lambda: 100.0
    )
    for candidate in (
        observation(drain_state="draining"),
        observation(revoked=True),
        observation(compatible=False),
        observation(drain_state="unrecognized"),
    ):
        assert evaluator.evaluate(candidate, probe()).admission_allowed is False
