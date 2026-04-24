from agent.services.admin_repair_contracts import build_admin_repair_mode_data, render_admin_repair_goal


def test_admin_repair_mode_data_contains_hook_ready_contract_fields():
    payload = build_admin_repair_mode_data(
        {
            "issue_symptom": "Service restart loop after update",
            "platform_target": "ubuntu",
            "execution_scope": "bounded_repair",
            "evidence_sources": "error_logs,service_status,runtime_state",
            "affected_targets": "hub-api",
        }
    )

    assert payload["platform_detection"]["platform_target"] == "ubuntu"
    assert payload["execution_scope"] == "bounded_repair"
    assert payload["dry_run"] is True
    assert payload["diagnosis_artifact"]["schema"] == "admin_repair_diagnosis_v1"
    assert payload["repair_plan"]["schema"] == "admin_repair_plan_v1"
    assert payload["repair_plan"]["dry_run_default"] is True
    assert payload["deterministic_repair_foundation"]["target_model"]["model_id"] == "deterministic_repair_path_v1"
    assert payload["deterministic_repair_foundation"]["normalized_evidence"]["schema"] == "deterministic_repair_evidence_v1"
    assert payload["deterministic_repair_foundation"]["signature_matching"]["schema"] == "deterministic_signature_matching_v1"
    assert payload["deterministic_repair_foundation"]["diagnosis_procedure_model"]["schema"] == "deterministic_diagnosis_procedure_v1"
    assert payload["deterministic_repair_foundation"]["repair_procedure_model"]["schema"] == "deterministic_repair_procedure_v1"
    assert payload["deterministic_repair_foundation"]["repair_catalog"]["schema"] == "deterministic_repair_catalog_v1"
    assert payload["deterministic_repair_foundation"]["repair_execution"]["apply_run"]["schema"] == "deterministic_repair_execution_v1"
    assert payload["deterministic_repair_foundation"]["outcome_tracking"]["schema"] == "deterministic_repair_outcome_tracking_v1"
    assert payload["deterministic_repair_foundation"]["success_weighted_recommendations"]["schema"] == "deterministic_success_weighted_recommendation_v1"
    assert payload["deterministic_repair_foundation"]["llm_escalation_policy"]["schema"] == "deterministic_llm_escalation_policy_v1"
    assert payload["deterministic_repair_foundation"]["repair_audit_chain"]["schema"] == "deterministic_repair_audit_chain_v1"

    required_fields = {
        "risk_class",
        "requires_approval",
        "dry_run_supported",
        "verification_required",
        "mutation_candidate",
        "evidence_sources",
        "execution_scope",
        "audit_hint",
        "repair_action_class",
        "affected_targets",
        "expected_verification",
    }
    for step in payload["repair_plan"]["steps"]:
        assert required_fields.issubset(step.keys())


def test_admin_repair_unknown_platform_forces_diagnosis_only_scope():
    payload = build_admin_repair_mode_data(
        {
            "issue_symptom": "Unknown runtime failure",
            "platform_target": "auto",
            "execution_scope": "bounded_repair",
        }
    )

    assert payload["platform_detection"]["platform_target"] == "unknown"
    assert payload["platform_detection"]["supported"] is False
    assert payload["execution_scope"] == "diagnosis_only"
    assert payload["repair_plan"]["execution_scope"] == "diagnosis_only"


def test_admin_repair_classifies_permissions_symptom():
    payload = build_admin_repair_mode_data(
        {
            "issue_symptom": "Permission denied while writing log file",
            "platform_target": "ubuntu",
        }
    )

    assert payload["diagnosis_artifact"]["problem_class"] == "permissions"


def test_admin_repair_goal_text_contains_foundation_signals():
    payload = build_admin_repair_mode_data(
        {
            "issue_symptom": "Container runtime is unhealthy",
            "platform_target": "windows11",
            "execution_scope": "bounded_repair",
        }
    )
    goal = render_admin_repair_goal(payload)

    assert "Shared Foundation" in goal
    assert "nicht voll KRITIS-gehaertet" in goal
    assert "Dry-run default: True" in goal


def test_admin_repair_execution_session_is_step_confirmed_and_bounded():
    payload = build_admin_repair_mode_data(
        {
            "issue_symptom": "Service restart loop",
            "platform_target": "ubuntu",
            "execution_scope": "bounded_repair",
        }
    )
    session = payload["execution_session"]

    assert session["execution_mode"] == "step_confirmed"
    assert session["allow_unmodeled_actions"] is False
    assert session["allow_unrestricted_shell_repair"] is False
    assert session["can_stop_between_steps"] is True
    assert session["steps"]
    assert any(step["confirmation_required"] for step in session["steps"])


def test_admin_repair_verification_phase_contains_result_states_and_checks():
    payload = build_admin_repair_mode_data(
        {
            "issue_symptom": "Container unhealthy after startup",
            "platform_target": "windows11",
            "execution_scope": "bounded_repair",
        }
    )
    verification = payload["verification_phase"]

    assert verification["schema"] == "admin_repair_verification_v1"
    assert verification["checks"]
    assert verification["result_state"] == "improved"
    assert "regressed" in verification["result_state_candidates"]
    assert "not_sufficient" in verification["note"]


def test_admin_repair_platform_adapters_and_playbooks_are_available_for_windows_and_ubuntu():
    windows_payload = build_admin_repair_mode_data(
        {
            "issue_symptom": "Docker compose service not healthy",
            "platform_target": "windows11",
        }
    )
    ubuntu_payload = build_admin_repair_mode_data(
        {
            "issue_symptom": "apt dependency mismatch",
            "platform_target": "ubuntu",
        }
    )

    assert windows_payload["platform_evidence_adapters"]["selected_adapter"]["platform"] == "windows11"
    assert ubuntu_payload["platform_evidence_adapters"]["selected_adapter"]["platform"] == "ubuntu"
    assert windows_payload["platform_playbooks"]["recommended_playbooks"]
    assert ubuntu_payload["platform_playbooks"]["recommended_playbooks"]


def test_admin_repair_bridge_contract_exposes_stable_hook_references():
    payload = build_admin_repair_mode_data(
        {
            "issue_symptom": "Runtime dependency failure",
            "platform_target": "ubuntu",
            "execution_scope": "bounded_repair",
        }
    )
    bridge = payload["bridge_contract"]

    assert bridge["schema"] == "admin_repair_bridge_contract_v1"
    assert bridge["session_bridge_id"].startswith("admin-repair-bridge-")
    assert bridge["bridge_actions"]
    first_action = bridge["bridge_actions"][0]
    assert first_action["audit_event_id"].startswith("audit:")
    assert first_action["mutation_gate_key"].startswith("mutation:")
    assert first_action["approval_policy_key"].startswith("approval:")
    assert first_action["sandbox_scope_key"].startswith("sandbox:")


def test_admin_repair_output_and_session_trail_are_structured_for_operator_view():
    payload = build_admin_repair_mode_data(
        {
            "issue_symptom": "Port already in use",
            "platform_target": "ubuntu",
            "execution_scope": "bounded_repair",
        }
    )
    cli_output = payload["cli_output"]
    session_trail = payload["session_trail"]

    section_ids = [section["id"] for section in cli_output["sections"]]
    assert section_ids == ["diagnosis", "plan", "risk", "verification"]
    assert cli_output["advisory_vs_enforced_visible"] is True
    assert session_trail["entries"]
    assert session_trail["entries"][-1]["event"] == "verification_phase"
    assert session_trail["sensitive_handling"]["redaction_enabled"] is True


def test_admin_repair_smoke_scenarios_cover_windows_and_ubuntu():
    payload = build_admin_repair_mode_data(
        {
            "issue_symptom": "Service cannot bind to port",
            "platform_target": "ubuntu",
            "execution_scope": "bounded_repair",
        }
    )
    scenarios = payload["smoke_scenarios"]

    assert len(scenarios) >= 2
    targets = {scenario["platform_target"] for scenario in scenarios}
    assert {"windows11", "ubuntu"}.issubset(targets)
    assert all(scenario["expects_confirmation_gates"] for scenario in scenarios)


def test_admin_repair_rollback_and_caution_model_is_explicit_for_risky_actions():
    payload = build_admin_repair_mode_data(
        {
            "issue_symptom": "Permission denied while changing ownership for runtime directory",
            "platform_target": "ubuntu",
            "execution_scope": "bounded_repair",
        }
    )
    rollback = payload["rollback_caution_model"]

    assert rollback["schema"] == "admin_repair_rollback_caution_v1"
    assert rollback["safe_presentation_guard"]["enabled"] is True
    assert rollback["non_reversible_action_ids"]
    assert rollback["caution_messages"]
    assert any("never present as safe" in item["message"] for item in rollback["caution_messages"])


def test_admin_repair_golden_paths_exist_for_windows_and_ubuntu():
    payload = build_admin_repair_mode_data(
        {
            "issue_symptom": "Service restart loop",
            "platform_target": "windows11",
            "execution_scope": "bounded_repair",
        }
    )
    golden_paths = payload["golden_paths"]

    assert golden_paths["schema"] == "admin_repair_golden_paths_v1"
    assert golden_paths["windows"]["platform_target"] == "windows11"
    assert golden_paths["ubuntu"]["platform_target"] == "ubuntu"
    assert golden_paths["windows"]["fixture_supported"] is True
    assert golden_paths["ubuntu"]["fixture_supported"] is True
    assert any(step["confirmation_gate"] for step in golden_paths["windows"]["steps"])
    assert any(step["confirmation_gate"] for step in golden_paths["ubuntu"]["steps"])


def test_admin_repair_future_extension_boundaries_are_explicit():
    payload = build_admin_repair_mode_data(
        {
            "issue_symptom": "Container runtime unstable",
            "platform_target": "ubuntu",
        }
    )
    boundaries = payload["future_extension_boundaries"]

    assert boundaries["schema"] == "admin_repair_extension_boundaries_v1"
    assert "network_specific_repair_architecture" in boundaries["out_of_scope_domains"]
    assert boundaries["extension_policy"]["requires_shared_repair_action_schema"] is True
    assert boundaries["extension_policy"]["forbid_parallel_repair_models"] is True
