import pytest

from agent.services.deterministic_repair_path_service import (
    DIAGNOSIS_PROCEDURE_MODEL,
    REPAIR_EXECUTION_SAFETY_POLICY,
    REPAIR_OUTCOME_MEMORY_MODEL,
    REPAIR_PROCEDURE_MODEL,
    REPAIR_PATH_TARGET_MODEL,
    REPAIR_PROBLEM_CLASS_INVENTORY,
    REPAIR_STATE_MODEL,
    REPAIR_VERIFICATION_MODEL,
    STANDARD_OUTCOME_LABELS,
    build_initial_repair_procedure_catalog,
    build_deterministic_repair_foundation_snapshot,
    build_initial_failure_signature_catalog,
    build_recovery_hint_bundle,
    build_repair_outcome_memory_entry,
    build_repair_procedure_preview,
    build_repair_procedure_template,
    build_signature_explanation,
    build_failure_signature,
    capture_command_result,
    classify_signature_matching_outcome,
    collect_environment_facts,
    evaluate_repair_confidence,
    execute_repair_procedure,
    get_initial_diagnosis_playbooks,
    ingest_structured_logs,
    match_failure_signatures,
    normalize_evidence_bundle,
    run_step_verification,
    run_diagnosis_playbook,
    select_repair_procedure_from_catalog,
    signature_to_dict,
    track_repair_outcomes,
    validate_non_destructive_diagnosis_playbook,
    verify_final_repair_outcome,
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


def test_initial_signature_catalog_contains_windows_ubuntu_and_shared_failures():
    catalog = build_initial_failure_signature_catalog()
    as_dicts = {item.id: signature_to_dict(item) for item in catalog}

    assert len(catalog) >= 7
    assert "sig-ubuntu-apt-lock-conflict" in as_dicts
    assert as_dicts["sig-ubuntu-apt-lock-conflict"]["environment_constraints"]["platform_target"] == "ubuntu"
    assert "sig-windows-service-timeout-1053" in as_dicts
    assert as_dicts["sig-windows-service-timeout-1053"]["environment_constraints"]["platform_target"] == "windows11"
    assert any(item["problem_class"] == "service_start_failure" for item in as_dicts.values())


def test_signature_matching_engine_ranks_matches_deterministically_without_llm():
    normalized = normalize_evidence_bundle(
        evidence_items=[
            {"type": "log_entry", "source": "error_logs", "severity": "error", "message": "service failed to start"},
            {"type": "log_entry", "source": "error_logs", "severity": "error", "message": "restart loop detected"},
            {"type": "command_result", "source": "service_status", "severity": "error", "command": "systemctl status api", "stderr": "start request repeated too quickly", "exit_code": 3},
        ],
        environment_facts={"platform_target": "ubuntu"},
    )
    result = match_failure_signatures(
        normalized_evidence=normalized,
        environment_facts={"platform_target": "ubuntu"},
    )

    assert result["schema"] == "deterministic_signature_matching_v1"
    assert result["llm_used"] is False
    assert result["matches"]
    assert result["matches"][0]["problem_class"] == "service_start_failure"
    assert result["matches"][0]["score"] >= 0.7


def test_signature_outcome_handles_ambiguity_and_low_confidence_paths():
    confidence_model = evaluate_repair_confidence(signature_strength=0.9, platform_match=1.0, history_success_rate=0.7)
    ambiguous = classify_signature_matching_outcome(
        ranked_matches=[
            {"signature_id": "a", "problem_class": "service_start_failure", "score": 0.82},
            {"signature_id": "b", "problem_class": "compose_failure", "score": 0.78},
        ],
        confidence_model=confidence_model,
    )
    low = classify_signature_matching_outcome(
        ranked_matches=[
            {"signature_id": "a", "problem_class": "service_start_failure", "score": 0.41},
        ],
        confidence_model=confidence_model,
    )

    assert ambiguous["outcome"] == "ambiguous_high_confidence"
    assert ambiguous["requires_review"] is True
    assert "run_branching_diagnosis_playbook" in ambiguous["recommended_next_steps"]
    assert low["outcome"] == "low_confidence"
    assert "avoid_mutation_until_confidence_improves" in low["recommended_next_steps"]


def test_signature_explanation_includes_key_evidence_and_constraints():
    normalized = normalize_evidence_bundle(
        evidence_items=[
            {"type": "log_entry", "source": "error_logs", "severity": "error", "message": "Address already in use on port 5000"},
            {"type": "log_entry", "source": "error_logs", "severity": "error", "message": "bind failed for service api"},
        ],
        environment_facts={"platform_target": "ubuntu"},
    )
    matching = match_failure_signatures(
        normalized_evidence=normalized,
        environment_facts={"platform_target": "ubuntu"},
    )
    explanation = build_signature_explanation(
        match=matching["matches"][0],
        normalized_evidence=normalized,
        environment_facts={"platform_target": "ubuntu"},
    )

    assert explanation["signature_id"]
    assert explanation["key_evidence"]
    assert "platform ubuntu" in explanation["summary"].lower()


def test_diagnosis_model_playbooks_and_runner_support_branching_and_early_stop():
    assert DIAGNOSIS_PROCEDURE_MODEL["schema"] == "deterministic_diagnosis_procedure_v1"
    playbooks = get_initial_diagnosis_playbooks()
    playbook = playbooks["service_start_failure"]
    validate_non_destructive_diagnosis_playbook(playbook)

    normalized = normalize_evidence_bundle(
        evidence_items=[
            {"type": "log_entry", "source": "service_status", "severity": "error", "message": "service failed to start"},
            {"type": "log_entry", "source": "error_logs", "severity": "error", "message": "restart loop"},
        ],
        environment_facts={"platform_target": "ubuntu"},
    )
    outcome = {
        "outcome": "single_high_confidence",
        "best_problem_class": "service_start_failure",
        "best_score": 0.88,
    }
    run = run_diagnosis_playbook(
        playbook=playbook,
        normalized_evidence=normalized,
        matching_outcome=outcome,
    )

    assert run["schema"] == "deterministic_diagnosis_run_v1"
    assert run["non_destructive_enforced"] is True
    assert run["executed_steps"]
    assert run["classification"] == "service_start_failure"
    assert run["final_state"] in {"classified", "completed"}


def test_diagnosis_non_destructive_policy_rejects_mutating_steps():
    playbook = {
        "id": "invalid-playbook",
        "steps": [
            {
                "id": "mutating-step",
                "step_type": "execute_mutation",
                "title": "should never be in diagnosis",
                "mutation_candidate": True,
            }
        ],
    }
    with pytest.raises(ValueError):
        validate_non_destructive_diagnosis_playbook(playbook)


def test_repair_procedure_model_supports_safety_classes_and_template_structure():
    template = build_repair_procedure_template(problem_class="permission_issue", platform_target="ubuntu")

    assert REPAIR_PROCEDURE_MODEL["schema"] == "deterministic_repair_procedure_v1"
    assert {"safe", "review_first", "high_risk"} == set(REPAIR_PROCEDURE_MODEL["safety_classes"])
    assert template["problem_class"] == "permission_issue"
    assert template["safety_class"] == "high_risk"
    assert template["preconditions"]
    assert template["postconditions"]
    assert template["verification"]["required"] is True
    assert template["rollback_hints"]


def test_initial_repair_catalog_ties_procedures_to_signatures_and_diagnosis_outcomes():
    signatures = build_initial_failure_signature_catalog()
    catalog = build_initial_repair_procedure_catalog(signature_catalog=signatures)
    entry = select_repair_procedure_from_catalog(
        repair_catalog=catalog,
        matching_outcome={"best_problem_class": "service_start_failure"},
    )

    assert catalog["schema"] == "deterministic_repair_catalog_v1"
    assert catalog["entries"]
    assert entry["problem_class"] == "service_start_failure"
    assert entry["trigger_signature_ids"]
    assert "service_start_failure" in entry["trigger_diagnosis_outcomes"]
    assert entry["procedure"]["steps"]


def test_repair_executor_supports_preview_safety_and_per_step_verification():
    catalog = build_initial_repair_procedure_catalog()
    selected = select_repair_procedure_from_catalog(
        repair_catalog=catalog,
        matching_outcome={"best_problem_class": "service_start_failure"},
    )
    normalized = normalize_evidence_bundle(
        evidence_items=[
            {"type": "log_entry", "source": "error_logs", "severity": "error", "message": "service failed to start"},
            {"type": "service_status", "source": "service_status", "severity": "error", "message": "service unhealthy"},
        ],
        environment_facts={"platform_target": "ubuntu"},
    )
    preview = build_repair_procedure_preview(
        selected_catalog_entry=selected,
        matching_outcome={"outcome": "single_high_confidence"},
    )
    dry_run = execute_repair_procedure(
        selected_catalog_entry=selected,
        normalized_evidence=normalized,
        environment_facts={"platform_target": "ubuntu"},
        dry_run=True,
    )

    assert preview["schema"] == "deterministic_repair_preview_v1"
    assert preview["limitations"]
    assert dry_run["schema"] == "deterministic_repair_execution_v1"
    assert dry_run["status"] == "preview_only"
    assert dry_run["safety_policy"]["schema"] == REPAIR_EXECUTION_SAFETY_POLICY["schema"]
    assert dry_run["steps"]
    assert all(step["verifiable"] for step in dry_run["steps"])


def test_repair_executor_stops_on_contradictory_or_worsening_evidence():
    catalog = build_initial_repair_procedure_catalog()
    selected = select_repair_procedure_from_catalog(
        repair_catalog=catalog,
        matching_outcome={"best_problem_class": "service_start_failure"},
    )
    contradictory_evidence = normalize_evidence_bundle(
        evidence_items=[
            {"type": "log_entry", "source": "error_logs", "severity": "error", "message": "service healthy but failed to start"},
            {"type": "log_entry", "source": "error_logs", "severity": "critical", "message": "panic and crash loop detected"},
        ],
        environment_facts={"platform_target": "ubuntu"},
    )
    run = execute_repair_procedure(
        selected_catalog_entry=selected,
        normalized_evidence=contradictory_evidence,
        environment_facts={"platform_target": "ubuntu"},
        dry_run=False,
        approval_policy={"approved_mutations": True},
    )

    assert run["status"] == "aborted"
    assert run["clean_stop"] is True
    assert run["abort_conditions"]
    assert run["stop_reason"] in {"worsening_signals", "contradictory_evidence"}


def test_verification_and_outcome_memory_tracking_are_standardized():
    step = {"id": "repair-step-02", "mutation_candidate": True}
    normalized = normalize_evidence_bundle(
        evidence_items=[
            {"type": "log_entry", "source": "error_logs", "severity": "info", "message": "service started and healthy"},
        ],
        environment_facts={"platform_target": "ubuntu"},
    )
    step_verification = run_step_verification(
        step=step,
        normalized_evidence=normalized,
        environment_facts={"platform_target": "ubuntu"},
    )

    catalog = build_initial_repair_procedure_catalog()
    selected = select_repair_procedure_from_catalog(
        repair_catalog=catalog,
        matching_outcome={"best_problem_class": "service_start_failure"},
    )
    execution = execute_repair_procedure(
        selected_catalog_entry=selected,
        normalized_evidence=normalized,
        environment_facts={"platform_target": "ubuntu"},
        dry_run=False,
        approval_policy={"approved_mutations": True},
    )
    final = verify_final_repair_outcome(
        execution_result=execution,
        normalized_evidence=normalized,
        matching_outcome={"outcome": "single_high_confidence"},
    )
    recovery = build_recovery_hint_bundle(
        selected_catalog_entry=selected,
        execution_result=execution,
    )
    memory_entry = build_repair_outcome_memory_entry(
        signature_matching={"matches": [{"signature_id": "sig-service-restart-loop"}]},
        selected_catalog_entry=selected,
        environment_facts={"platform_target": "ubuntu", "os_family": "linux", "package_manager": "apt_dpkg", "service_state": "healthy"},
        execution_result=execution,
        final_verification=final,
    )
    tracking = track_repair_outcomes([memory_entry])

    assert REPAIR_VERIFICATION_MODEL["schema"] == "deterministic_repair_verification_v1"
    assert REPAIR_OUTCOME_MEMORY_MODEL["schema"] == "deterministic_repair_outcome_memory_v1"
    assert step_verification["schema"] == "deterministic_step_verification_v1"
    assert final["schema"] == "deterministic_repair_final_verification_v1"
    assert final["outcome_label"] in set(STANDARD_OUTCOME_LABELS)
    assert recovery["schema"] == "deterministic_repair_recovery_hints_v1"
    assert memory_entry["schema"] == "deterministic_repair_outcome_memory_entry_v1"
    assert tracking["schema"] == "deterministic_repair_outcome_tracking_v1"
    assert tracking["counts_by_outcome"][memory_entry["outcome_label"]] >= 1


def test_foundation_snapshot_contains_first_thirty_task_artifacts():
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
    assert snapshot["initial_signature_catalog"]["schema"] == "deterministic_failure_signature_catalog_v1"
    assert snapshot["initial_signature_catalog"]["entries"]
    assert snapshot["signature_matching"]["schema"] == "deterministic_signature_matching_v1"
    assert snapshot["signature_matching_outcome"]["outcome"] in {
        "single_high_confidence",
        "ambiguous_high_confidence",
        "low_confidence",
        "no_match",
    }
    assert snapshot["diagnosis_procedure_model"]["schema"] == "deterministic_diagnosis_procedure_v1"
    assert snapshot["diagnosis_playbooks"]["entries"]
    assert snapshot["diagnosis_run"]["schema"] == "deterministic_diagnosis_run_v1"
    assert snapshot["non_destructive_diagnosis_policy"]["enforced"] is True
    assert snapshot["repair_procedure_model"]["schema"] == "deterministic_repair_procedure_v1"
    assert snapshot["repair_procedure_template"]["preconditions"]
    assert snapshot["repair_catalog"]["schema"] == "deterministic_repair_catalog_v1"
    assert snapshot["selected_repair_catalog_entry"]["procedure"]["id"]
    assert snapshot["repair_preview"]["schema"] == "deterministic_repair_preview_v1"
    assert snapshot["repair_execution"]["dry_run"]["status"] == "preview_only"
    assert snapshot["repair_execution"]["apply_run"]["schema"] == "deterministic_repair_execution_v1"
    assert snapshot["verification_model"]["schema"] == "deterministic_repair_verification_v1"
    assert snapshot["final_repair_verification"]["schema"] == "deterministic_repair_final_verification_v1"
    assert snapshot["recovery_hints"]["schema"] == "deterministic_repair_recovery_hints_v1"
    assert snapshot["outcome_memory_model"]["schema"] == "deterministic_repair_outcome_memory_v1"
    assert snapshot["outcome_memory_entry"]["schema"] == "deterministic_repair_outcome_memory_entry_v1"
    assert snapshot["outcome_tracking"]["schema"] == "deterministic_repair_outcome_tracking_v1"
