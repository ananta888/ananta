from agent.services.planning_quality_service import get_planning_quality_service


def test_new_software_project_uses_default_validation_profile_when_policy_missing() -> None:
    quality = get_planning_quality_service().evaluate(
        subtasks=[
            {
                "title": "Skeleton",
                "description": "Create initial folder only.",
                "task_kind": "coding",
            }
        ],
        mode="new_software_project",
        planning_policy={"validation_profiles": {}},
        team_id=None,
    )
    assert quality.ok is False
    assert "too_few_tasks" in quality.reason


def test_generic_mode_uses_default_validation_profile_when_policy_missing() -> None:
    quality = get_planning_quality_service().evaluate(
        subtasks=[],
        mode="generic",
        planning_policy={},
        team_id=None,
    )
    assert quality.ok is False
    assert "too_few_tasks" in quality.reason
