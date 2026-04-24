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
