import pytest

from agent.services.deterministic_repair_path_service import (
    REPAIR_PATH_TARGET_MODEL,
    REPAIR_PROBLEM_CLASS_INVENTORY,
    REPAIR_STATE_MODEL,
    build_deterministic_repair_foundation_snapshot,
    build_failure_signature,
    capture_command_result,
    collect_environment_facts,
    evaluate_repair_confidence,
    ingest_structured_logs,
    normalize_evidence_bundle,
    signature_to_dict,
)


def test_target_model_inventory_and_state_model_are_defined():
    assert REPAIR_PATH_TARGET_MODEL["strategy"] == "deterministic_first_llm_escalation"
    assert "diagnosing" in REPAIR_PATH_TARGET_MODEL["phases"]
    assert "service_start_failure" in REPAIR_PROBLEM_CLASS_INVENTORY
    assert "approval_required" in REPAIR_STATE_MODEL["states"]
    assert "verifying" in REPAIR_STATE_MODEL["transitions"]["executing"]


def test_confidence_model_decision_thresholds_are_explainable():
    high = evaluate_repair_confidence(signature_strength=0.9, platform_match=1.0, history_success_rate=0.8)
    medium = evaluate_repair_confidence(signature_strength=0.62, platform_match=0.8, history_success_rate=0.5)
    low = evaluate_repair_confidence(signature_strength=0.2, platform_match=0.3, history_success_rate=0.1)

    assert high["decision"] == "deterministic_execute"
    assert medium["decision"] in {"review_required", "deterministic_execute"}
    assert low["decision"] == "llm_escalation"
    assert set(high["components"].keys()) == {"signature_strength", "platform_match", "history_success_rate"}


def test_environment_fact_collection_captures_platform_runtime_and_service_context():
    facts = collect_environment_facts(
        {
            "platform_target": "ubuntu",
            "runtime_versions": {"python": "3.12.3"},
            "container_state": "running",
            "service_state": "degraded",
        }
    )
    assert facts["platform_target"] == "ubuntu"
    assert facts["os_family"] == "linux"
    assert facts["package_manager"] == "apt_dpkg"
    assert facts["runtime_versions"]["python"] == "3.12.3"
    assert facts["service_state"] == "degraded"


def test_log_ingestion_and_command_capture_produce_structured_evidence():
    logs = ingest_structured_logs(
        [
            {"message": "Service failed to start", "severity": "error", "timestamp": "2026-04-24T13:00:00Z"},
            "warning: dependency may be missing",
        ],
        source="journal",
    )
    cmd = capture_command_result(
        command="systemctl status api",
        exit_code=3,
        stdout="inactive",
        stderr="failed",
        health_check="unhealthy",
    )
    assert len(logs) == 2
    assert logs[0]["provenance"]["ingested_from"] == "journal"
    assert logs[1]["severity"] in {"warning", "error", "info"}
    assert cmd["type"] == "command_result"
    assert cmd["status"] == "failure"
    assert cmd["health_check"] == "unhealthy"


def test_evidence_normalization_pipeline_reduces_noise_and_keeps_diagnostic_signal():
    normalized = normalize_evidence_bundle(
        evidence_items=[
            {"type": "log_entry", "source": "journal", "severity": "error", "message": "service failed"},
            {"type": "log_entry", "source": "journal", "severity": "error", "message": "service failed"},
            {"type": "health_check", "source": "probe", "severity": "warning", "message": "probe degraded"},
        ],
        environment_facts={"platform_target": "ubuntu"},
    )
    assert normalized["schema"] == "deterministic_repair_evidence_v1"
    assert normalized["metrics"]["ingested_count"] == 3
    assert normalized["metrics"]["normalized_count"] == 2
    assert normalized["metrics"]["dropped_noise_count"] == 1


def test_failure_signature_model_supports_patterns_constraints_and_weights():
    signature = build_failure_signature(
        {
            "id": "sig-service-restart-loop",
            "problem_class": "service_start_failure",
            "evidence_patterns": [r"restart loop", r"failed to start"],
            "structured_fields": ["service_name", "exit_code"],
            "environment_constraints": {"platform_target": "ubuntu"},
            "confidence_weight": 1.4,
        }
    )
    as_dict = signature_to_dict(signature)
    compiled = signature.compiled_patterns()
    assert signature.problem_class == "service_start_failure"
    assert len(compiled) == 2
    assert as_dict["environment_constraints"]["platform_target"] == "ubuntu"
    assert as_dict["confidence_weight"] == pytest.approx(1.4)


def test_failure_signature_model_rejects_unknown_problem_class():
    with pytest.raises(ValueError):
        build_failure_signature(
            {
                "id": "sig-unknown",
                "problem_class": "unknown_problem",
                "evidence_patterns": [r"some pattern"],
            }
        )


def test_foundation_snapshot_contains_first_ten_task_artifacts():
    snapshot = build_deterministic_repair_foundation_snapshot(
        mode_data={"platform_target": "windows11"},
        issue_symptom="Service failed to start after update",
        evidence_sources=["error_logs", "service_status", "runtime_state"],
    )
    assert snapshot["target_model"]["model_id"] == "deterministic_repair_path_v1"
    assert "package_install_failure" in snapshot["problem_class_inventory"]
    assert "states" in snapshot["state_model"]
    assert snapshot["confidence_model"]["decision"] in {"deterministic_execute", "review_required", "llm_escalation"}
    assert "allowed_evidence_types" in snapshot["evidence_ingestion_model"]
    assert snapshot["environment_facts"]["platform_target"] == "windows11"
    assert snapshot["normalized_evidence"]["schema"] == "deterministic_repair_evidence_v1"
    assert snapshot["failure_signature_model"]["schema"] == "failure_signature_v1"
