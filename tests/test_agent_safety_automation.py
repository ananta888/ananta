from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.agent_safety_admission_policy import AgentSafetyAdmissionPolicy
from agent.services.agent_safety_evaluation_service import AgentSafetyEvaluationService
from agent.services.agent_safety_ports import RecordingSafetyAdapter, SafetyControlReceipt
from agent.services.agent_safety_recovery_service import AgentSafetyRecoveryService
from agent.services.agent_safety_retention_service import AgentSafetyRetentionService
from agent.services.agent_safety_runtime_adapters import (
    DockerAgentSafetyRuntime,
    DockerEgressFenceAdapter,
    DockerForensicSnapshotAdapter,
    DockerSandboxSafetyControlAdapter,
    HubCredentialLeaseAuthority,
)
from agent.services.agent_safety_service import AgentSafetyControlService
from agent.services.agent_safety_state_store import AgentSafetyStateStore
from agent.services.agent_safety_training_adapter import HubQueuedSafetyTrainingAdapter
from agent.services.ops_command_runner import CommandResult
from ananta_contracts.agent_safety import SafetyAction


class FakeDockerRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def exists(self, binary: str) -> bool:
        return binary == "docker"

    def run(self, args, *, cwd=None, timeout_seconds=None, env=None):
        del cwd, timeout_seconds, env
        command = tuple(args)
        self.calls.append(command)
        if command[:3] == ("docker", "version", "--format"):
            return CommandResult(0, '"25.0"', "")
        if command[:4] == ("docker", "inspect", "--format", "{{json .State}}"):
            return CommandResult(0, '{"Running":true,"Paused":false,"Status":"running"}', "")
        if command[:4] == ("docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}"):
            return CommandResult(0, '{"safe-net":{},"tool-net":{}}', "")
        return CommandResult(0, "", "")


class RecordingCleanup:
    def __init__(self) -> None:
        self.sandboxes: list[str] = []

    def cleanup(self, *, operation_id: str, run_id: str, sandbox_id: str) -> SafetyControlReceipt:
        self.sandboxes.append(sandbox_id)
        return SafetyControlReceipt(
            operation_id,
            run_id,
            sandbox_id,
            SafetyAction.TERMINATE,
            True,
            "sandbox_cleanup_enforced",
            "2026-08-30T00:00:00Z",
            "cleanup-test",
            False,
        )


def _control_service(tmp_path: Path):
    store = AgentSafetyStateStore(tmp_path / "automation.sqlite3")
    adapter = RecordingSafetyAdapter()
    leases = HubCredentialLeaseAuthority(store)
    service = AgentSafetyControlService(
        store,
        manifest_signing_key=b"a" * 32,
        sandbox_control=adapter,
        egress_fence=adapter,
        credential_revocation=leases,
        credential_lease_authority=leases,
        admission_policy=AgentSafetyAdmissionPolicy(store),
    )
    service.configure_policy(
        policy_id="policy-auto",
        revision=1,
        mode="adversarial_eval",
        preventive_policy_enabled=False,
        preventive_training_enabled=True,
        telemetry_enabled=True,
        external_kill_switch_enabled=True,
        incident_freeze_enabled=True,
        sentinel_enabled=True,
        adversarial_evaluation_enabled=True,
        adversarial_scope=["local:fixture"],
        global_stop_scope="run",
        max_parallel_agents=100,
        automatic_authorization=True,
    )
    return store, service, leases


def test_docker_adapter_enforces_freeze_egress_snapshot_and_exact_allowlist(tmp_path: Path) -> None:
    runner = FakeDockerRunner()
    runtime = DockerAgentSafetyRuntime(
        runner=runner,
        managed_sandboxes=["sandbox-1"],
        snapshot_root=tmp_path / "snapshots",
    )

    assert runtime.ready() is True
    controls = DockerSandboxSafetyControlAdapter(runtime)
    egress = DockerEgressFenceAdapter(runtime)
    snapshots = DockerForensicSnapshotAdapter(runtime)
    assert (
        controls.apply(
            operation_id="operation-1",
            run_id="run-1",
            sandbox_id="sandbox-1",
            action=SafetyAction.FREEZE,
            reason="boundary",
        ).runtime_verified
        is True
    )
    assert egress.deny(operation_id="operation-1", run_id="run-1", sandbox_id="sandbox-1").enforced is True
    snapshot = snapshots.capture(operation_id="operation-1", run_id="run-1", sandbox_id="sandbox-1", max_bytes=16_384)
    assert snapshot["captured"] is True
    assert (tmp_path / "snapshots" / snapshot["artifact_ref"]).is_file()
    assert ("docker", "pause", "sandbox-1") in runner.calls
    assert (
        "docker",
        "network",
        "disconnect",
        "--force",
        "safe-net",
        "sandbox-1",
    ) in runner.calls
    with pytest.raises(ValueError, match="not_pre_authorized"):
        egress.deny(operation_id="operation-2", run_id="run-1", sandbox_id="unknown")


def test_hub_credential_leases_are_short_lived_run_bound_and_revocable(tmp_path: Path) -> None:
    store = AgentSafetyStateStore(tmp_path / "leases.sqlite3")
    authority = HubCredentialLeaseAuthority(store)
    grant = authority.issue(run_id="run-1", agent_id="agent-1", ttl_seconds=60)

    assert authority.verify(run_id="run-1", lease_id=grant.lease_id, token=grant.token) is True
    assert authority.verify(run_id="run-2", lease_id=grant.lease_id, token=grant.token) is False
    receipt = authority.revoke(operation_id="operation-1", run_id="run-1")
    assert receipt.runtime_verified is True
    assert authority.verify(run_id="run-1", lease_id=grant.lease_id, token=grant.token) is False


def test_parallel_identity_runtime_telemetry_and_automatic_lease_revocation(tmp_path: Path) -> None:
    store, service, leases = _control_service(tmp_path)
    agents = [{"agent_id": f"agent-{index}", "sandbox_id": f"sandbox-{index}"} for index in range(100)]
    registered = service.register_run(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id="run-1",
        group_id="group-1",
        policy_id="policy-auto",
        target_ref="local:fixture",
        agents=agents,
    )
    assert len(registered["credential_lease_grants"]) == 100
    event = service.record_runtime_event(
        run_id="run-1",
        sandbox_id="sandbox-0",
        agent_id="agent-0",
        event_type="tool_call",
        detector_id="runtime-detector",
        metadata={"tool_name": "shell", "credential_token": "redact", "command": "opaque"},
    )
    assert event["details"]["detail_level"] == "high_fidelity"
    assert event["details"]["credential_token"] == "[REDACTED]"
    service.emergency_stop(run_id="run-1", action="freeze", reason="automatic-test")
    first = registered["credential_lease_grants"][0]
    assert leases.verify(run_id="run-1", lease_id=first["lease_id"], token=first["token"]) is False
    assert store.get("policy_authorization", "policy-auto:1")["decision"] == "allow"


def test_hub_registers_one_distinct_run_sandbox_and_lease_per_parallel_agent(tmp_path: Path) -> None:
    _store, service, _leases = _control_service(tmp_path)
    result = service.register_run_group(
        tenant_id="tenant-1",
        project_id="project-1",
        group_id="red-team-group",
        run_prefix="red-team-run",
        policy_id="policy-auto",
        target_ref="local:fixture",
        agents=[
            {"agent_id": f"parallel-agent-{index}", "sandbox_id": f"parallel-sandbox-{index}"} for index in range(100)
        ],
    )
    assert result["parallel_agent_count"] == 100
    assert len(set(result["run_ids"])) == 100
    assert all(len(run["credential_lease_grants"]) == 1 for run in result["runs"])


def test_retention_cleans_only_expired_freezes_with_incident_evidence(tmp_path: Path) -> None:
    store = AgentSafetyStateStore(tmp_path / "retention.sqlite3")
    store.append(
        "run",
        "run-1",
        {
            "run_id": "run-1",
            "state": "freeze",
            "freeze_expires_at": "2026-08-29T00:00:00Z",
            "execution_allowed": False,
            "agents": [{"sandbox_id": "sandbox-1"}],
        },
        expected_revision=0,
    )
    store.append("incident_bundle", "bundle-1", {"bundle_id": "bundle-1", "run_id": "run-1"}, expected_revision=0)
    cleanup = RecordingCleanup()
    result = AgentSafetyRetentionService(store, cleanup=cleanup).sweep_expired(now="2026-08-30T00:00:00Z")
    assert result["state"] == "completed"
    assert cleanup.sandboxes == ["sandbox-1"]
    assert store.get("run", "run-1")["state"] == "cleaned"


def test_evaluation_grids_cascades_training_and_fix_regression_are_headless(tmp_path: Path) -> None:
    store, service, _leases = _control_service(tmp_path)
    evaluation = AgentSafetyEvaluationService(
        store,
        series_signing_key=b"e" * 32,
        training_adapter=HubQueuedSafetyTrainingAdapter(store),
    )
    matrix = evaluation.build_distribution_shift_matrix(
        matrix_id="matrix-1",
        toolsets=["tools-a", "tools-b"],
        sandboxes=["sandbox-a"],
        symbol_names=["symbol-a", "symbol-b"],
        goals=["goal-a"],
    )
    assert matrix["scenario_count"] == 4
    awareness = evaluation.evaluate_awareness_variants(
        evaluation_id="awareness-1",
        trials=[
            {"context_hint": "training", "triggered": True},
            {"context_hint": "evaluation", "triggered": True},
            {"context_hint": "production", "triggered": False},
            {"context_hint": "unspecified", "triggered": True},
        ],
    )
    assert awareness["evaluation_awareness_detected"] is True
    cascade = evaluation.evaluate_trigger_cascade(
        evaluation_id="cascade-1",
        trials=[
            {"trigger_ids": ["trigger-a", "trigger-b"], "triggered_ids": ["trigger-a"]},
            {"trigger_ids": ["trigger-a", "trigger-b"], "triggered_ids": ["trigger-b"]},
        ],
    )
    assert cascade["cascade_trigger_rate"] == 1.0
    assert cascade["redundancy_gain"] == 0.5

    service.register_run(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id="run-regression",
        group_id="group-1",
        policy_id="policy-auto",
        target_ref="local:fixture",
        agents=[{"agent_id": "agent-1", "sandbox_id": "sandbox-1"}],
    )
    stopped = service.emergency_stop(run_id="run-regression", action="freeze", reason="escape")
    bundle_id = stopped["incident"]["bundle_id"]
    recovery = AgentSafetyRecoveryService(store)
    recovery.classify_incident(
        bundle_id=bundle_id,
        causes=["runtime_vulnerability"],
        disposition="patched",
        patch_digest="patch-digest",
    )
    exact = recovery.create_replay(bundle_id=bundle_id, replay_id="replay-exact", target_ref="local:fixture")
    mutated = recovery.create_replay(
        bundle_id=bundle_id,
        replay_id="replay-mutated",
        target_ref="local:fixture",
        mutation={"encoding": "alternate"},
    )
    verified = recovery.verify_fix(
        bundle_id=bundle_id,
        verification_id="verification-1",
        results=[
            {
                "variant": "exact",
                "replay_id": exact["replay_id"],
                "contained": True,
                "security_invariant_restored": True,
            },
            {
                "variant": "mutated",
                "replay_id": mutated["replay_id"],
                "contained": True,
                "security_invariant_restored": True,
            },
        ],
    )
    assert verified["state"] == "passed"
    assert verified["human_intervention_required"] is False
