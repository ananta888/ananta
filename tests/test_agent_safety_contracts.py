from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ananta_contracts.agent_safety import (
    SafetyAction,
    SafetyEvent,
    SafetyMode,
    SafetyPolicy,
    SentinelManifest,
    StopScope,
    TriggerClass,
)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def test_mandatory_controls_cannot_be_disabled_by_prevention_mode() -> None:
    with pytest.raises(ValueError, match="mandatory_controls_disabled"):
        SafetyPolicy(
            policy_id="policy-1",
            revision=1,
            mode=SafetyMode.OBSERVE_ONLY,
            preventive_policy_enabled=False,
            preventive_training_enabled=False,
            telemetry_enabled=False,
            external_kill_switch_enabled=True,
            incident_freeze_enabled=True,
        )


def test_adversarial_policy_accepts_only_explicit_local_targets() -> None:
    with pytest.raises(ValueError, match="adversarial_scope_not_local"):
        SafetyPolicy(
            policy_id="policy-1",
            revision=1,
            mode=SafetyMode.ADVERSARIAL_EVAL,
            preventive_policy_enabled=False,
            preventive_training_enabled=False,
            telemetry_enabled=True,
            external_kill_switch_enabled=True,
            incident_freeze_enabled=True,
            adversarial_scope=("https://third-party.example",),
        )


def test_sentinel_manifest_is_run_bound_expiring_and_tamper_evident() -> None:
    now = datetime.now(timezone.utc)
    key = b"k" * 32
    manifest = SentinelManifest(
        manifest_id="manifest-1",
        tenant_id="tenant-1",
        project_id="project-1",
        run_id="run-1",
        sandbox_id="sandbox-1",
        trigger_id="opaque-1",
        trigger_class=TriggerClass.OPAQUE_PRIORITY,
        policy_id="policy-1",
        policy_revision=1,
        policy_mode=SafetyMode.ENFORCE,
        manifest_version=1,
        nonce="nonce-1",
        issued_at=_iso(now - timedelta(seconds=1)),
        expires_at=_iso(now + timedelta(minutes=1)),
        effect=SafetyAction.FREEZE,
        priority=100,
        visibility="opaque",
    ).sign(key)

    manifest.verify(key, now=_iso(now), expected_run_id="run-1", expected_sandbox_id="sandbox-1")
    with pytest.raises(ValueError, match="binding_mismatch"):
        manifest.verify(key, now=_iso(now), expected_run_id="run-2", expected_sandbox_id="sandbox-1")
    with pytest.raises(ValueError, match="signature_invalid"):
        SentinelManifest(**{**manifest.unsigned_payload(), "signature": "0" * 64}).verify(
            key,
            now=_iso(now),
            expected_run_id="run-1",
            expected_sandbox_id="sandbox-1",
        )


def test_safety_event_redacts_credentials_and_is_hash_chained() -> None:
    event = SafetyEvent(
        event_id="event-1",
        tenant_id="tenant-1",
        project_id="project-1",
        run_id="run-1",
        sandbox_id="sandbox-1",
        agent_id="agent-1",
        event_type="boundary_crossing",
        severity="critical",
        source="detector-1",
        observed_at="2026-08-29T00:00:00Z",
        details={"credential_token": "never-store", "path": "/workspace"},
        previous_digest="a" * 64,
    ).as_dict()

    assert event["details"]["credential_token"] == "[REDACTED]"
    assert event["details"]["path"] == "/workspace"
    assert len(event["event_digest"]) == 64


def test_policy_supports_bounded_group_stop_without_human_gate() -> None:
    policy = SafetyPolicy(
        policy_id="policy-1",
        revision=1,
        mode=SafetyMode.ENFORCE,
        preventive_policy_enabled=True,
        preventive_training_enabled=False,
        telemetry_enabled=True,
        external_kill_switch_enabled=True,
        incident_freeze_enabled=True,
        global_stop_scope=StopScope.GROUP,
        max_parallel_agents=100,
    )
    assert policy.as_dict()["global_stop_scope"] == "group"
