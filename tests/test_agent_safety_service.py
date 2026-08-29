from __future__ import annotations

import pytest

from agent.services.agent_safety_evaluation_service import AgentSafetyEvaluationService
from agent.services.agent_safety_ports import (
    RecordingSafetyAdapter,
    UnavailableCredentialRevocation,
    UnavailableEgressFence,
    UnavailableSafetyControl,
)
from agent.services.agent_safety_recovery_service import AgentSafetyRecoveryService
from agent.services.agent_safety_service import AgentSafetyControlService, AgentSafetyDenied
from agent.services.agent_safety_state_store import AgentSafetyStateConflictError, AgentSafetyStateStore


def _service(tmp_path, *, recording=True):
    store = AgentSafetyStateStore(tmp_path / "safety.sqlite3")
    if recording:
        adapter = RecordingSafetyAdapter()
        service = AgentSafetyControlService(
            store,
            manifest_signing_key=b"s" * 32,
            sandbox_control=adapter,
            egress_fence=adapter,
            credential_revocation=adapter,
        )
    else:
        adapter = None
        service = AgentSafetyControlService(
            store,
            manifest_signing_key=b"s" * 32,
            sandbox_control=UnavailableSafetyControl(),
            egress_fence=UnavailableEgressFence(),
            credential_revocation=UnavailableCredentialRevocation(),
        )
    return store, service, AgentSafetyRecoveryService(store), AgentSafetyEvaluationService(store), adapter


def _policy(service, *, scope="run", mode="enforce", targets=None, parallel=2):
    return service.configure_policy(
        policy_id="policy-1",
        revision=1,
        mode=mode,
        preventive_policy_enabled=mode != "adversarial_eval",
        preventive_training_enabled=False,
        telemetry_enabled=True,
        external_kill_switch_enabled=True,
        incident_freeze_enabled=True,
        adversarial_scope=targets or [],
        global_stop_scope=scope,
        max_parallel_agents=parallel,
    )


def _run(
    service,
    run_id="run-1",
    sandbox_id="sandbox-1",
    agent_id="agent-1",
    group_id="group-1",
    target="local:fixture",
):
    return service.register_run(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id=run_id,
        group_id=group_id,
        policy_id="policy-1",
        target_ref=target,
        agents=[{"agent_id": agent_id, "sandbox_id": sandbox_id}],
    )


def test_adversarial_run_admission_is_local_automatic_and_fail_closed(tmp_path) -> None:
    _store, service, _recovery, _evaluation, _adapter = _service(tmp_path)
    _policy(service, mode="adversarial_eval", targets=["local:fixture"])
    assert _run(service)["mode"] == "adversarial_eval"
    with pytest.raises(AgentSafetyDenied, match="target_not_authorized"):
        _run(service, run_id="run-2", target="local:other")


def test_opaque_sentinel_is_hub_issued_consumed_once_and_stops_atomically(tmp_path) -> None:
    store, service, _recovery, _evaluation, adapter = _service(tmp_path)
    _policy(service)
    _run(service)
    manifest = service.issue_sentinel(
        run_id="run-1",
        sandbox_id="sandbox-1",
        trigger_class="opaque_priority",
        effect="freeze",
    )
    result = service.consume_sentinel(manifest=manifest, agent_id="agent-1")

    assert result["event"]["event_type"] == "sentinel_triggered"
    assert result["containment"]["state"] == "enforced"
    assert store.get("run", "run-1")["execution_allowed"] is False
    assert len(adapter.receipts) == 3
    with pytest.raises(AgentSafetyStateConflictError, match="revision_conflict"):
        service.consume_sentinel(manifest=manifest, agent_id="agent-1")


def test_tampered_or_cross_run_manifest_never_executes_control(tmp_path) -> None:
    _store, service, _recovery, _evaluation, adapter = _service(tmp_path)
    _policy(service)
    _run(service)
    manifest = service.issue_sentinel(
        run_id="run-1",
        sandbox_id="sandbox-1",
        trigger_class="shutdown",
        effect="terminate",
    )
    with pytest.raises(ValueError, match="signature_invalid"):
        service.consume_sentinel(manifest={**manifest, "run_id": "run-x"}, agent_id="agent-1")
    assert adapter.receipts == []


def test_event_ingress_rejects_agent_sandbox_spoofing(tmp_path) -> None:
    _store, service, _recovery, _evaluation, _adapter = _service(tmp_path)
    _policy(service)
    _run(service)
    with pytest.raises(AgentSafetyDenied, match="agent_sandbox_binding_mismatch"):
        service.record_boundary_event(
            run_id="run-1",
            sandbox_id="sandbox-1",
            agent_id="agent-spoofed",
            boundary_class="network",
            outcome="crossed",
            detector_id="external-detector",
        )


def test_external_boundary_detector_can_freeze_an_entire_group(tmp_path) -> None:
    store, service, _recovery, _evaluation, adapter = _service(tmp_path)
    _policy(service, scope="group")
    _run(service, run_id="run-1", sandbox_id="sandbox-1", agent_id="agent-1")
    _run(service, run_id="run-2", sandbox_id="sandbox-2", agent_id="agent-2")

    event = service.record_boundary_event(
        run_id="run-1",
        sandbox_id="sandbox-1",
        agent_id="agent-1",
        boundary_class="network",
        outcome="crossed",
        detector_id="network-detector",
        metadata={"credential_token": "must-redact"},
    )

    assert event["details"]["credential_token"] == "[REDACTED]"
    assert event["containment"]["scope"] == "group"
    assert store.get("run", "run-1")["execution_allowed"] is False
    assert store.get("run", "run-2")["execution_allowed"] is False
    assert len(adapter.receipts) == 6
    overview = service.overview(project_id="project-1")
    assert overview["metrics"]["boundary_outcomes"] == {"crossed": 1}
    assert overview["metrics"]["boundary_classes"] == {"network": 1}
    assert overview["metrics"]["external_observations"] == 1


def test_unavailable_runtime_control_fails_closed_and_preserves_incident(tmp_path) -> None:
    store, service, _recovery, _evaluation, _adapter = _service(tmp_path, recording=False)
    _policy(service)
    _run(service)
    result = service.emergency_stop(run_id="run-1", action="terminate", reason="external-detector")
    assert result["state"] == "failed_closed"
    assert store.get("run", "run-1")["state"] == "containment_failed_closed"
    assert store.get("run", "run-1")["execution_allowed"] is False
    assert result["incident"]["redaction_applied"] is True


def test_patch_before_retry_requires_disposition_and_local_target(tmp_path) -> None:
    _store, service, recovery, _evaluation, _adapter = _service(tmp_path)
    _policy(service)
    _run(service)
    stopped = service.emergency_stop(run_id="run-1", action="freeze", reason="escape")
    bundle_id = stopped["incident"]["bundle_id"]
    with pytest.raises(AgentSafetyDenied, match="patch_before_retry"):
        recovery.create_replay(bundle_id=bundle_id, replay_id="replay-1", target_ref="local:fixture")
    recovery.classify_incident(
        bundle_id=bundle_id,
        causes=["runtime_vulnerability", "monitoring_gap"],
        disposition="patched",
        patch_digest="f" * 64,
    )
    replay = recovery.create_replay(
        bundle_id=bundle_id,
        replay_id="replay-1",
        target_ref="local:fixture",
        mutation={"variant": "path-normalization"},
    )
    assert replay["patch_digest"] == "f" * 64
    with pytest.raises(AgentSafetyDenied, match="replay_target_not_local"):
        recovery.create_replay(bundle_id=bundle_id, replay_id="replay-2", target_ref="https:external")


def test_training_records_require_holdout_and_do_not_expose_opaque_effect(tmp_path) -> None:
    _store, service, _recovery, evaluation, _adapter = _service(tmp_path)
    _policy(service)
    records = evaluation.compile_training_records(
        policy_id="policy-1",
        manifests=[
            {"trigger_class": "opaque_priority", "trigger_id": "abc", "split": "train"},
            {"trigger_class": "opaque_priority", "trigger_id": "def", "split": "holdout"},
        ],
    )
    assert all(item["effect_label_exposed"] is False for item in records["records"])
    assert len(records["dataset_digest"]) == 64


def test_evaluation_aggregates_profiles_without_claiming_release_evidence(tmp_path) -> None:
    _store, service, _recovery, evaluation, _adapter = _service(tmp_path)
    result = evaluation.evaluate_trials(
        evaluation_id="eval-1",
        trials=[
            {"profile_id": "model-a", "triggered": True, "contained": True, "self_reported": True},
            {"profile_id": "model-a", "triggered": False, "contained": True, "self_reported": False},
        ],
    )
    assert result["profiles"]["model-a"]["trigger_compliance_rate"] == 0.5
    assert result["profiles"]["model-a"]["containment_rate"] == 1.0
    assert result["profiles"]["model-a"]["self_reported"] == 1
    assert result["grounded_release_evidence"] is False
    assert service.overview()["human_intervention_required"] is False


def test_trigger_rotation_is_reproducible_but_train_and_holdout_are_disjoint(tmp_path) -> None:
    first_store = AgentSafetyStateStore(tmp_path / "first.sqlite3")
    second_store = AgentSafetyStateStore(tmp_path / "second.sqlite3")
    first = AgentSafetyEvaluationService(first_store, series_signing_key=b"r" * 32).build_trigger_series(
        series_id="series-1", train_count=3, holdout_count=2
    )
    second = AgentSafetyEvaluationService(second_store, series_signing_key=b"r" * 32).build_trigger_series(
        series_id="series-1", train_count=3, holdout_count=2
    )
    assert first["symbols"] == second["symbols"]
    train = {item["symbol"] for item in first["symbols"] if item["split"] == "train"}
    holdout = {item["symbol"] for item in first["symbols"] if item["split"] == "holdout"}
    assert train.isdisjoint(holdout)


def test_optional_training_is_bounded_without_a_human_wait(tmp_path) -> None:
    _store, service, _recovery, evaluation, _adapter = _service(tmp_path)
    _policy(service)
    result = evaluation.submit_training(
        policy_id="policy-1",
        channel="preventive_boundary",
        dataset_digest="unused-while-disabled",
        records=[],
    )
    assert result == {
        "state": "skipped_disabled",
        "policy_id": "policy-1",
        "channel": "preventive_boundary",
        "human_intervention_required": False,
    }
    with pytest.raises(AgentSafetyDenied, match="training_adapter_unavailable"):
        evaluation.submit_training(
            policy_id="policy-1",
            channel="sentinel_priority",
            dataset_digest="unverified",
            records=[],
        )
