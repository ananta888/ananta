import pytest
from pydantic import ValidationError

from agent.services.worker_selection_policy_service import (
    WorkerSelectionPolicyError,
    WorkerSelectionPolicyService,
    external_analysis_only_policy,
    policy_allows_kind,
    strict_local_policy,
)
from worker.core.runtime_target import WorkerKind, WorkerSelectionMode, WorkerSelectionPolicy


def test_fixed_policy_requires_worker_id_or_kind():
    with pytest.raises(ValidationError):
        WorkerSelectionPolicy(mode=WorkerSelectionMode.fixed)


def test_fixed_policy_accepts_worker_kind():
    policy = WorkerSelectionPolicy(
        mode=WorkerSelectionMode.fixed,
        fixed_worker_kind=WorkerKind.native_ananta_worker,
    )
    assert policy.fixed_worker_kind == WorkerKind.native_ananta_worker


def test_automatic_policy_forbids_fixed_worker_id():
    with pytest.raises(ValidationError):
        WorkerSelectionPolicy(
            mode=WorkerSelectionMode.automatic,
            fixed_worker_id="opencode-local-01",
        )


def test_unknown_worker_kind_rejected():
    with pytest.raises(ValidationError):
        WorkerSelectionPolicy(allowed_worker_kinds=["unknown-worker"])


def test_strict_local_policy_denies_hermes_external_worker():
    policy = strict_local_policy()
    allowed, reason = policy_allows_kind(policy, WorkerKind.hermes.value)
    assert allowed is False
    assert reason in {"worker_kind_not_in_allowlist", "external_worker_denied_allow_external_false"}


def test_external_analysis_policy_allows_hermes_but_not_cloud():
    policy = external_analysis_only_policy()
    allowed, reason = policy_allows_kind(policy, WorkerKind.hermes.value)
    assert allowed is True
    assert reason == ""
    assert policy.allow_cloud is False
    assert policy.allow_external_workers is True


def test_legacy_preferred_backend_maps_to_fixed_worker_policy():
    service = WorkerSelectionPolicyService()
    policy = service.from_config({"preferred_backend": "opencode", "risk_profile": "strict"})
    assert policy.mode == WorkerSelectionMode.fixed
    assert policy.fixed_worker_kind == WorkerKind.opencode
    assert policy.allowed_worker_kinds == [WorkerKind.opencode]
    assert policy.fallback_policy.value == "deny"


def test_unknown_legacy_preferred_backend_rejected():
    service = WorkerSelectionPolicyService()
    with pytest.raises(WorkerSelectionPolicyError):
        service.from_config({"preferred_backend": "magic-cloud-agent"})


def test_service_roundtrip_preserves_worker_selection_payload():
    service = WorkerSelectionPolicyService()
    policy = WorkerSelectionPolicy(
        mode=WorkerSelectionMode.policy_ranked,
        allowed_worker_kinds=[WorkerKind.native_ananta_worker, WorkerKind.opencode],
        required_capabilities=["repair.execute.inspect"],
        allow_cloud=False,
        allow_external_workers=False,
    )
    cfg = service.to_config(policy)
    restored = service.from_config(cfg)
    assert restored == policy
