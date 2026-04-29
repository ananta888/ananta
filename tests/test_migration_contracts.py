from __future__ import annotations


def test_legacy_task_shape_is_still_readable_via_task_detail(client, admin_auth_header):
    from agent.db_models import TaskDB
    from agent.repository import task_repo

    task_repo.save(
        TaskDB(
            id="LEGACY-TASK-READ-1",
            title="Legacy task",
            description="record without newer optional fields",
            status="todo",
        )
    )

    response = client.get("/tasks/LEGACY-TASK-READ-1", headers=admin_auth_header)
    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["id"] == "LEGACY-TASK-READ-1"
    assert payload["status"] == "todo"
    assert "history" in payload


def test_legacy_task_api_remains_usable_when_goal_flags_are_disabled(client, app, admin_auth_header):
    cfg = dict(app.config.get("AGENT_CONFIG") or {})
    flags = dict(cfg.get("feature_flags") or {})
    flags["goal_workflow_enabled"] = False
    flags["persisted_plans_enabled"] = False
    cfg["feature_flags"] = flags
    app.config["AGENT_CONFIG"] = cfg

    create = client.post(
        "/tasks",
        headers=admin_auth_header,
        json={"title": "Legacy API task", "description": "compat path", "status": "todo"},
    )
    assert create.status_code in {200, 201}
    payload = create.get_json().get("data") or {}
    task_id = payload.get("id")
    assert task_id

    detail = client.get(f"/tasks/{task_id}", headers=admin_auth_header)
    assert detail.status_code == 200
