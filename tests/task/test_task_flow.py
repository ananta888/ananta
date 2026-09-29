from unittest.mock import patch


def test_task_propose_and_execute(
    client,
    app,
    admin_auth_header,
):
    """Simuliert einen Task-Flow: Propose und Execute."""

    from agent.services.task_runtime_service import (
        update_local_task_status,
    )

    with app.app_context():
        update_local_task_status(
            "task-123",
            "todo",
            description="ordinary legacy-compatible task",
        )

    # 1. Propose Step
    propose_data = {"task_id": "task-123", "prompt": "Berechne 2+2"}

    # Wir müssen den LLM-Call mocken
    with patch("agent.routes.tasks.execution._call_llm") as mock_llm:
        mock_llm.return_value = '{"reason": "Einfache Berechnung.", "command": "echo 4"}'

        response = client.post("/step/propose", json=propose_data, headers=admin_auth_header)

    assert response.status_code == 200
    assert "command" in response.json["data"]
    assert "echo 4" in response.json["data"]["command"]

    # 2. Execute Step
    execute_data = {"task_id": "task-123", "command": "echo 4"}

    # Wir müssen die Shell-Execution mocken
    with patch("agent.shell.PersistentShell.execute") as mock_exec:
        mock_exec.return_value = ("4", 0)

        # Auth wird hier übersprungen, da AGENT_TOKEN in der Config leer sein könnte (Standard in Tests)
        # Falls Auth aktiv ist, müssten wir einen Header mitschicken.
        response = client.post("/step/execute", json=execute_data, headers=admin_auth_header)

    assert response.status_code == 200
    assert response.json["data"]["exit_code"] == 0
    assert response.json["data"]["output"] == "4"


def test_global_step_routes_reject_unknown_task_before_side_effect(
    client,
    app,
    admin_auth_header,
):
    from agent.services.task_runtime_service import (
        get_local_task_status,
    )

    with (
        patch(
            "agent.routes.tasks.execution._call_llm"
        ) as llm,
        patch(
            "agent.shell.PersistentShell.execute"
        ) as shell,
    ):
        propose = client.post(
            "/step/propose",
            json={
                "task_id": "unknown-global-task",
                "prompt": "do not run",
            },
            headers=admin_auth_header,
        )
        execute = client.post(
            "/step/execute",
            json={
                "task_id": "unknown-global-task",
                "command": "echo unsafe",
            },
            headers=admin_auth_header,
        )

    assert propose.status_code == 409
    assert execute.status_code == 409
    assert propose.json["message"] == (
        "legacy_task_id_not_authoritative"
    )
    assert llm.call_count == 0
    assert shell.call_count == 0
    with app.app_context():
        assert get_local_task_status(
            "unknown-global-task"
        ) is None


def test_global_step_routes_reject_recovery_task_before_side_effect(
    client,
    app,
    admin_auth_header,
):
    from agent.services.task_runtime_service import (
        get_local_task_status,
        update_local_task_status,
    )

    with app.app_context():
        update_local_task_status(
            "legacy-source",
            "blocked_by_dependency",
        )
        update_local_task_status(
            "legacy-recovery-child",
            "todo",
            source_task_id="legacy-source",
            derivation_reason="goal_task_recovery",
            status_reason_details={
                "model_recovery_release": {
                    "release_epoch": "epoch",
                }
            },
        )
        before = get_local_task_status(
            "legacy-recovery-child"
        )
    with (
        patch(
            "agent.routes.tasks.execution._call_llm"
        ) as llm,
        patch(
            "agent.shell.PersistentShell.execute"
        ) as shell,
    ):
        propose = client.post(
            "/step/propose",
            json={
                "task_id": "legacy-recovery-child",
                "prompt": "do not run",
            },
            headers=admin_auth_header,
        )
        execute = client.post(
            "/step/execute",
            json={
                "task_id": "legacy-recovery-child",
                "command": "echo unsafe",
            },
            headers=admin_auth_header,
        )

    assert propose.status_code == 409
    assert execute.status_code == 409
    assert propose.json["message"] == (
        "recovery_task_requires_scoped_endpoint"
    )
    assert llm.call_count == 0
    assert shell.call_count == 0
    with app.app_context():
        after = get_local_task_status(
            "legacy-recovery-child"
        )
    assert after["status"] == before["status"]
    assert after["history"] == before["history"]
