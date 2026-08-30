from agent.services.agent_safety_evaluation_service import AgentSafetyEvaluationService
from agent.services.agent_safety_ports import RecordingSafetyAdapter
from agent.services.agent_safety_recovery_service import AgentSafetyRecoveryService
from agent.services.agent_safety_service import AgentSafetyControlService
from agent.services.agent_safety_state_store import AgentSafetyStateStore


def test_automatic_boundary_stop_incident_patch_and_mutated_replay(tmp_path) -> None:
    store = AgentSafetyStateStore(tmp_path / "chaos.sqlite3")
    adapter = RecordingSafetyAdapter()
    controls = AgentSafetyControlService(
        store,
        manifest_signing_key=b"c" * 32,
        sandbox_control=adapter,
        egress_fence=adapter,
        credential_revocation=adapter,
    )
    recovery = AgentSafetyRecoveryService(store)
    evaluation = AgentSafetyEvaluationService(store)
    controls.configure_policy(
        policy_id="chaos-policy",
        revision=1,
        mode="adversarial_eval",
        preventive_policy_enabled=False,
        preventive_training_enabled=False,
        telemetry_enabled=True,
        external_kill_switch_enabled=True,
        incident_freeze_enabled=True,
        adversarial_scope=["local:escape-fixture"],
        global_stop_scope="group",
        max_parallel_agents=100,
    )
    controls.configure_policy(
        policy_id="sentinel-policy",
        revision=1,
        mode="adversarial_eval",
        preventive_policy_enabled=False,
        preventive_training_enabled=False,
        telemetry_enabled=True,
        external_kill_switch_enabled=True,
        incident_freeze_enabled=True,
        adversarial_scope=["local:escape-fixture"],
        global_stop_scope="run",
        max_parallel_agents=1,
    )
    controls.register_run(
        tenant_id="tenant-1",
        project_id="project-1",
        run_id="sentinel-run",
        group_id="sentinel-group",
        policy_id="sentinel-policy",
        target_ref="local:escape-fixture",
        agents=[{"agent_id": "sentinel-agent", "sandbox_id": "sentinel-sandbox"}],
    )
    manifest = controls.issue_sentinel(
        run_id="sentinel-run",
        sandbox_id="sentinel-sandbox",
        trigger_class="opaque_priority",
        effect="freeze",
    )
    triggered = controls.consume_sentinel(manifest=manifest, agent_id="sentinel-agent")
    assert triggered["event"]["event_type"] == "sentinel_triggered"
    assert triggered["containment"]["scope"] == "run"
    for index in range(3):
        controls.register_run(
            tenant_id="tenant-1",
            project_id="project-1",
            run_id=f"run-{index}",
            group_id="group-1",
            policy_id="chaos-policy",
            target_ref="local:escape-fixture",
            agents=[{"agent_id": f"agent-{index}", "sandbox_id": f"sandbox-{index}"}],
        )

    event = controls.record_boundary_event(
        run_id="run-1",
        sandbox_id="sandbox-1",
        agent_id="agent-1",
        boundary_class="orchestration",
        outcome="crossed",
        detector_id="hub-assignment-detector",
        metadata={"attempt": "worker_to_worker_dispatch"},
    )
    containment = event["containment"]
    assert containment["state"] == "enforced"
    assert containment["scope"] == "group"
    assert len(containment["receipts"]) == 9
    assert all(not store.get("run", f"run-{index}")["execution_allowed"] for index in range(3))

    bundle = containment["incident"]
    recovery.classify_incident(
        bundle_id=bundle["bundle_id"],
        causes=["runtime_vulnerability", "monitoring_gap"],
        disposition="patched",
        patch_digest="patch-fixture-digest",
    )
    exact = recovery.create_replay(
        bundle_id=bundle["bundle_id"],
        replay_id="replay-exact",
        target_ref="local:escape-fixture",
    )
    mutated = recovery.create_replay(
        bundle_id=bundle["bundle_id"],
        replay_id="replay-mutated",
        target_ref="local:escape-fixture",
        mutation={"agent_count": 10, "path_variant": "encoded"},
    )
    assert exact["source_bundle_digest"] == mutated["source_bundle_digest"]

    report = evaluation.evaluate_trials(
        evaluation_id="chaos-evaluation",
        trials=[
            {
                "profile_id": "fixture-policy",
                "triggered": True,
                "contained": True,
                "self_reported": False,
            }
        ],
    )
    assert report["profiles"]["fixture-policy"]["contained"] == 1
    assert report["grounded_release_evidence"] is False
