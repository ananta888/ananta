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


def test_generic_mode_accepts_german_implementation_signals() -> None:
    quality = get_planning_quality_service().evaluate(
        subtasks=[
            {
                "title": "API Implementierung umsetzen",
                "description": "Implementierung des Fibonacci-Endpunkts inklusive Python-Code und Run-Command.",
                "task_kind": "",
            },
            {
                "title": "Tests anlegen",
                "description": "Erstelle Testdatei fuer API-Validierung und Edge-Cases.",
                "task_kind": "testing",
            },
            {
                "title": "README aktualisieren",
                "description": "Dokumentiere Setup, Startkommando und Testausfuehrung.",
                "task_kind": "doc",
            },
        ],
        mode="generic",
        planning_policy={},
        team_id=None,
    )
    assert quality.ok is True
    assert quality.reason == "ok"
