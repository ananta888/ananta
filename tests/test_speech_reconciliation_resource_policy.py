from __future__ import annotations

import pytest

from agent.services.speech_reconciliation_resource_policy import (
    RESOURCE_MODES,
    SpeechReconciliationResourcePolicy,
    SpeechReconciliationResourcePolicyError,
    SpeechReconciliationResourceRequest,
)


def _request(**changes) -> SpeechReconciliationResourceRequest:
    values = {
        "mode": "immediate",
        "requested_factor": 10,
        "user_max_factor": 5,
        "live_call_active": False,
        "foreground_load_micros": 100_000,
        "charging": True,
        "minute_of_day": 60,
    }
    values.update(changes)
    return SpeechReconciliationResourceRequest(**values)


def test_every_mode_yields_to_live_calls_and_user_factor_is_a_hard_cap() -> None:
    policy = SpeechReconciliationResourcePolicy()
    for mode in RESOURCE_MODES:
        changes = {"mode": mode, "live_call_active": True}
        if mode == "scheduled":
            changes.update(schedule_start_minute=0, schedule_end_minute=120)
        decision = policy.evaluate(_request(**changes))
        assert decision.allowed is False and decision.action == "pause"
    admitted = policy.evaluate(_request())
    assert admitted.allowed is True and admitted.effective_factor == 5


def test_idle_charging_schedule_quiet_hours_and_foreground_pressure_are_fail_closed() -> None:
    policy = SpeechReconciliationResourcePolicy()
    assert policy.evaluate(_request(mode="charging_only", charging=False)).reason_code.endswith("not_charging")
    assert policy.evaluate(_request(quiet_hours=True)).reason_code.endswith("quiet_hours")
    assert policy.evaluate(_request(foreground_load_micros=700_001)).reason_code.endswith(
        "foreground_pressure"
    )
    outside = policy.evaluate(
        _request(
            mode="scheduled",
            minute_of_day=300,
            schedule_start_minute=1380,
            schedule_end_minute=120,
        )
    )
    inside = policy.evaluate(
        _request(
            mode="scheduled",
            minute_of_day=30,
            schedule_start_minute=1380,
            schedule_end_minute=120,
        )
    )
    assert outside.allowed is False and inside.allowed is True


def test_invalid_or_cross_mode_schedule_configuration_is_rejected() -> None:
    policy = SpeechReconciliationResourcePolicy()
    with pytest.raises(SpeechReconciliationResourcePolicyError, match="mode_invalid"):
        policy.evaluate(_request(mode="automatic"))
    with pytest.raises(SpeechReconciliationResourcePolicyError, match="schedule_invalid"):
        policy.evaluate(_request(mode="scheduled"))
    with pytest.raises(SpeechReconciliationResourcePolicyError, match="schedule_forbidden"):
        policy.evaluate(_request(schedule_start_minute=1))
