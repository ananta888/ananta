from agent.services.reference_profile_service import get_reference_profile_service


def test_starter_reference_profiles_cover_expected_stacks():
    service = get_reference_profile_service()

    profiles = service.list_profiles()
    profile_ids = {item["profile_id"] for item in profiles}

    assert len(profiles) == 3
    assert "ref.java.keycloak" in profile_ids
    assert "ref.python.ananta_backend" in profile_ids
    assert "ref.angular.ananta_frontend" in profile_ids
    for profile in profiles:
        assert profile["model_fields"] == [
            "language",
            "framework",
            "project_type",
            "reference_source",
            "strengths",
            "limitations",
            "intended_usage",
        ]
        assert profile["strengths"]
        assert profile["limitations"]
        assert profile["reference_source"]["repo"]


def test_usage_boundary_defines_guidance_without_blind_copy():
    service = get_reference_profile_service()

    boundary = service.usage_boundary()

    assert boundary["mode"] == "guidance_not_clone"
    assert "blind file copy" in boundary["forbidden_reuse"]
    assert "policy" in boundary["governance_guardrail"]


def test_selection_strategy_is_deterministic_for_known_combinations():
    service = get_reference_profile_service()

    java = service.select_profile(
        language="java",
        project_type="backend_security_service",
        flow="new_project",
    )
    python = service.select_profile(
        language="python",
        project_type="backend_orchestration_service",
        flow="project_evolution",
    )
    angular = service.select_profile(
        language="typescript",
        project_type="admin_workflow_frontend",
        flow="new_project",
    )

    assert java is not None and java["profile"]["profile_id"] == "ref.java.keycloak"
    assert python is not None and python["profile"]["profile_id"] == "ref.python.ananta_backend"
    assert angular is not None and angular["profile"]["profile_id"] == "ref.angular.ananta_frontend"


def test_usage_audit_marker_binds_profile_and_source():
    service = get_reference_profile_service()

    marker = service.build_usage_audit_marker(
        profile_id="ref.python.ananta_backend",
        flow="project_evolution",
        task_or_goal_id="goal-123",
    )

    assert marker["reference_profile_id"] == "ref.python.ananta_backend"
    assert marker["flow"] == "project_evolution"
    assert marker["task_or_goal_id"] == "goal-123"
    assert marker["reference_source_repo"] == "ananta888/ananta"
