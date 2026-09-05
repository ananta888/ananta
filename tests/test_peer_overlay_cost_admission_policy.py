from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.peer_overlay_cost_admission_policy import PeerOverlayCostAdmissionPolicy


def policy() -> PeerOverlayCostAdmissionPolicy:
    value = json.loads(Path("config/peer_overlay_cost_budgets.default.json").read_text(encoding="utf-8"))
    value["tenant_profiles"] = {"tenant-test": "local-browser-test-v1"}
    return PeerOverlayCostAdmissionPolicy.from_mapping(value)


def observation(*, tenant_id: str = "tenant-test", now: int = 10_000, turn: int = 0, peer: int = 0):
    return {
        "tenant_id": tenant_id,
        "window_started_at_seconds": now - 10,
        "turn_egress_bytes": turn,
        "peer_relay_egress_bytes": peer,
    }


def evaluate(cost_policy: PeerOverlayCostAdmissionPolicy, **overrides):
    values = {
        "tenant_id": "tenant-test",
        "turn_edges": 1,
        "peer_relay_edges": 2,
        "observation": observation(),
        "strict_e2ee_ready": True,
        "relay_consent_complete": True,
        "minimum_quality_met": True,
        "now_seconds": 10_000,
    }
    values.update(overrides)
    return cost_policy.evaluate(**values)


def test_versioned_budget_exposes_environment_and_evidence_metadata() -> None:
    decision = evaluate(policy())

    assert decision.allowed is True
    assert decision.profile_version == "1.0.0"
    assert decision.budget_evidence == {
        "evidence_revision": "peer-nat-matrix-v1",
        "evidence_scope": "test",
        "browser": "chromium148-firefox153",
        "hardware_class": "i7-13700h-20cpu-64gb",
        "network_profile": "local-direct-turn-udp-tcp",
        "measurement_duration_seconds": 6,
        "window_seconds": 60,
    }


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"observation": None}, "peer_overlay_cost_observation_missing"),
        (
            {"observation": observation(now=100), "now_seconds": 10_000},
            "peer_overlay_cost_observation_stale",
        ),
        ({"turn_edges": 2}, "peer_overlay_turn_quota_exceeded"),
        (
            {"observation": observation(turn=16_000_000)},
            "peer_overlay_turn_quota_exceeded",
        ),
        ({"peer_relay_edges": 5}, "peer_overlay_peer_relay_quota_exceeded"),
        (
            {"observation": observation(peer=30_000_000)},
            "peer_overlay_peer_relay_quota_exceeded",
        ),
    ],
)
def test_missing_or_excessive_tenant_metrics_reduce_admission(overrides, reason) -> None:
    decision = evaluate(policy(), **overrides)

    assert decision.allowed is False
    assert decision.reason_code == reason


@pytest.mark.parametrize(
    ("flag", "reason"),
    [
        ("strict_e2ee_ready", "peer_overlay_strict_e2ee_required"),
        ("relay_consent_complete", "peer_overlay_relay_consent_required"),
        ("minimum_quality_met", "peer_overlay_minimum_quality_required"),
    ],
)
def test_cost_savings_never_override_security_consent_or_quality(flag, reason) -> None:
    decision = evaluate(policy(), turn_edges=0, peer_relay_edges=0, observation=None, **{flag: False})

    assert decision.allowed is False
    assert decision.reason_code == reason


def test_cost_observation_cannot_cross_tenant_boundary() -> None:
    with pytest.raises(ValueError, match="tenant_mismatch"):
        evaluate(policy(), observation=observation(tenant_id="tenant-other"))


def test_unmeasured_default_profile_fails_closed_for_metered_edges() -> None:
    decision = evaluate(
        policy(),
        tenant_id="tenant-production",
        turn_edges=0,
        peer_relay_edges=1,
        observation=observation(tenant_id="tenant-production"),
    )

    assert decision.allowed is False
    assert decision.reason_code == "peer_overlay_peer_relay_quota_exceeded"
