from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.mail_runtime_policy import (
    MailCircuitBreaker,
    MailRolloutPhase,
    MailRuntimePolicy,
    PassiveMailHealthRegistry,
)


def test_runtime_defaults_to_network_off() -> None:
    policy = MailRuntimePolicy.from_environment({})

    assert policy.snapshot().phase is MailRolloutPhase.INFRASTRUCTURE_OFF
    assert policy.snapshot().network_enabled is False
    with pytest.raises(PermissionError, match="mail_runtime_network_disabled"):
        policy.require_network_operation("sync")


def test_circuit_breaker_state_survives_process_reconstruction(
    tmp_path: Path,
) -> None:
    now = [100.0]
    state_path = tmp_path / "circuit.json"
    breaker = MailCircuitBreaker(
        failure_threshold=2,
        cooldown_seconds=30,
        clock=lambda: now[0],
        state_path=state_path,
    )
    breaker.record_failure(account_id="primary", provider="jmap")
    breaker.record_failure(account_id="primary", provider="jmap")

    reconstructed = MailCircuitBreaker(
        failure_threshold=2,
        cooldown_seconds=30,
        clock=lambda: now[0],
        state_path=state_path,
    )
    assert reconstructed.state(account_id="primary", provider="jmap") == "open"
    assert reconstructed.allow(account_id="primary", provider="jmap") is False

    now[0] = 131.0
    assert reconstructed.allow(account_id="primary", provider="jmap") is True
    assert reconstructed.state(account_id="primary", provider="jmap") == "half_open"
    reconstructed.record_success(account_id="primary", provider="jmap")
    assert reconstructed.state(account_id="primary", provider="jmap") == "closed"


def test_health_snapshot_is_passive() -> None:
    health = PassiveMailHealthRegistry(clock=lambda: 42.0)
    health.observe("config", status="ok", reason_code="mail_config_valid")

    snapshot = health.snapshot()

    assert snapshot["mode"] == "passive"
    assert snapshot["network_calls"] == 0
    assert snapshot["components"]["config"]["observed_at"] == 42.0
