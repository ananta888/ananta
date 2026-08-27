from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent.services.local_adapter_lifecycle import LocalAdapterReleasePolicy
from agent.services.local_adapter_rollout_service import (
    LocalAdapterCanaryController,
    LocalAdapterRolloutRepository,
    LocalAdapterShadowController,
)


def _policy(*, tools: tuple[str, ...] = ("lookup",), minimum_shadow: int = 1):
    return LocalAdapterReleasePolicy(
        policy_id="release-policy-v1",
        target="needle2",
        evaluation_seed=42,
        latency_limit_ms=100.0,
        memory_limit_bytes=1024,
        max_slice_regression=0.01,
        minimum_shadow_examples=minimum_shadow,
        minimum_shadow_match_rate=1.0,
        minimum_canary_examples=1,
        maximum_canary_error_rate=0.01,
        minimum_canary_accuracy=0.99,
        maximum_canary_escalation_rate=0.01,
        canary_latency_limit_ms=100.0,
        maximum_confidence_brier_score=0.1,
        canary_traffic_basis_points=1000,
        canary_allowed_tools=tools,
        canary_maximum_duration_seconds=86_400,
    )


class _Lease:
    def __init__(self, valid=True):
        self.is_valid = valid

    def valid(self, **_kwargs):
        return self.is_valid


def test_shadow_is_persistent_hash_bound_and_has_no_execution_port(tmp_path) -> None:
    repository = LocalAdapterRolloutRepository(tmp_path / "rollout.sqlite3")
    controller = LocalAdapterShadowController(repository)
    digest = "a" * 64
    policy = _policy()

    controller.observe(
        interaction_id="interaction-1",
        dataset_sha256=digest,
        candidate_sha256="b" * 64,
        policy_sha256=policy.digest,
        production={"tool": "lookup", "arguments": {"query": "safe"}},
        candidate={"tool": "lookup", "arguments": {"query": "safe"}, "risk_class": "read"},
        production_latency_ms=20,
        candidate_latency_ms=10,
    )
    evidence = LocalAdapterShadowController(repository).evidence(
        dataset_sha256=digest,
        candidate_sha256="b" * 64,
        release_policy=policy,
    )

    assert evidence.examples == evidence.matches == 1
    assert evidence.unsafe_actions == 0
    assert not hasattr(controller, "execute")


def test_canary_is_sampled_fenced_and_blocks_live_writes(tmp_path, monkeypatch) -> None:
    repository = LocalAdapterRolloutRepository(tmp_path / "rollout.sqlite3")
    controller = LocalAdapterCanaryController(
        repository,
        leases=_Lease(),
        release_policy=_policy(tools=("update",)),
        dataset_sha256="a" * 64,
        candidate_sha256="b" * 64,
        expires_at="2026-01-02T00:00:00Z",
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(
        "agent.services.local_adapter_rollout_service.hashlib.sha256",
        lambda _value: type("Digest", (), {"hexdigest": lambda self: "0" * 64})(),
    )

    blocked = controller.authorize(
        interaction_id="i-1",
        tool_name="update",
        risk_class="write",
        dry_run=False,
        lease_id="lease-1",
        fencing_token=7,
    )
    admitted = controller.authorize(
        interaction_id="i-2",
        tool_name="update",
        risk_class="write",
        dry_run=True,
        lease_id="lease-1",
        fencing_token=7,
    )

    assert blocked.reason_code == "canary_side_effect_blocked"
    assert admitted.admitted is True
    controller.record_outcome(
        interaction_id="i-2",
        slice_id="read",
        success=True,
        accurate=True,
        escalated=False,
        latency_ms=12,
    )
    evidence = controller.evidence()
    assert evidence.examples == 1
    assert evidence.error_rate == 0.0
    assert evidence.slice_metrics["read"]["accuracy"] == 1.0
    with pytest.raises(ValueError, match="interaction_conflict"):
        controller.record_outcome(
            interaction_id="i-2",
            slice_id="read",
            success=False,
            accurate=False,
            escalated=True,
            latency_ms=99,
        )
    assert controller.evidence().examples == 1


def test_canary_outcome_fails_closed_after_lease_loss(tmp_path, monkeypatch) -> None:
    repository = LocalAdapterRolloutRepository(tmp_path / "rollout.sqlite3")
    lease = _Lease()
    controller = LocalAdapterCanaryController(
        repository,
        leases=lease,
        release_policy=_policy(),
        dataset_sha256="a" * 64,
        candidate_sha256="b" * 64,
        expires_at="2026-01-02T00:00:00Z",
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(
        "agent.services.local_adapter_rollout_service.hashlib.sha256",
        lambda _value: type("Digest", (), {"hexdigest": lambda self: "0" * 64})(),
    )
    assert (
        controller.authorize(
            interaction_id="lease-loss",
            tool_name="lookup",
            risk_class="read",
            dry_run=False,
            lease_id="lease-1",
            fencing_token=7,
        ).admitted
        is True
    )

    lease.is_valid = False
    with pytest.raises(ValueError, match="canary_lease_invalid"):
        controller.record_outcome(
            interaction_id="lease-loss",
            slice_id="read",
            success=True,
            accurate=True,
            escalated=False,
            latency_ms=3,
        )
    assert repository.read("canary_outcomes") == ()
