from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = Path(__file__).with_name("failure_scenarios.v1.json")

REQUIRED_SCENARIOS = {
    "worker_process_loss_history_replay",
    "hub_crash_after_task_acceptance",
    "temporal_server_restart_inflight_recovery",
    "activity_heartbeat_loss_retry",
    "activity_start_to_close_timeout",
    "cancel_propagation_with_uncertain_activity",
    "bounded_retry_and_non_idempotent_suppression",
    "direct_signal_cancel_race",
    "n_minus_one_history_replay",
    "non_idempotent_acknowledgement_uncertainty",
    "history_threshold_fail_closed",
    "state_threshold_fail_closed",
}


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_failure_manifest_names_at_least_ten_release_blocking_scenarios() -> None:
    manifest = _manifest()
    scenarios = manifest["scenarios"]
    scenario_ids = {scenario["id"] for scenario in scenarios}

    assert manifest["schema"] == "ananta.temporal-failure-scenarios.v1"
    assert manifest["minimum_scenarios"] >= 10
    assert len(scenarios) >= manifest["minimum_scenarios"]
    assert len(scenario_ids) == len(scenarios)
    assert REQUIRED_SCENARIOS <= scenario_ids
    assert all(scenario["release_blocking"] is True for scenario in scenarios)
    assert manifest["release_requirements"] == {
        "n_minus_one_replay": True,
        "real_compose_gate": True,
        "structured_artifacts": True,
        "temporal_test_environment": True,
    }


def test_manifest_test_nodes_exist_and_critical_race_repeats_ten_times() -> None:
    manifest = _manifest()
    scenarios = {scenario["id"]: scenario for scenario in manifest["scenarios"]}
    minimum = manifest["critical_race_minimum_repetitions"]

    assert minimum >= 10
    assert scenarios["direct_signal_cancel_race"]["minimum_repetitions"] >= minimum
    assert scenarios["n_minus_one_history_replay"]["release_blocking"] is True
    for scenario in scenarios.values():
        node = scenario["test_node"]
        path_text, _, test_name = node.partition("::")
        path_text = path_text.split("#", 1)[0]
        path = ROOT / path_text
        assert path.is_file(), node
        if test_name:
            source = path.read_text(encoding="utf-8")
            assert f"def {test_name}(" in source, node


def test_ci_gate_requires_sdk_environment_real_restarts_and_structured_evidence() -> None:
    workflow_source = (ROOT / ".github/workflows/quality-and-docs.yml").read_text(encoding="utf-8")
    compose_source = (ROOT / "docker/compose-next/compose.tests.temporal.yml").read_text(encoding="utf-8")

    required_ci_fragments = (
        "tests/workflow_runtime/temporal",
        "--junitxml=ci-artifacts/temporal-failure-gate/test-environment.xml",
        "ANANTA_TEMPORAL_SMOKE_MODE=start-recovery",
        "restart temporal",
        "kill -s SIGKILL ananta-temporal-worker",
        "test-environment-summary.json",
        "server-restart-evidence.json",
        "worker-crash-evidence.json",
    )
    for fragment in required_ci_fragments:
        assert fragment in workflow_source

    assert "temporal-smoke:" in compose_source
    assert "read_only: true" in compose_source
    assert 'cap_drop: ["ALL"]' in compose_source
    assert "ANANTA_TEMPORAL_API_KEY" not in compose_source
