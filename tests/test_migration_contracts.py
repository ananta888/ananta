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


def test_legacy_config_missing_new_keys_gets_safe_defaults(client, app, admin_auth_header):
    app.config["AGENT_CONFIG"] = {"llm_backend": "openai", "command_timeout": 30}

    response = client.get("/config", headers=admin_auth_header)
    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["llm_backend"] == "openai"
    assert payload["command_timeout"] == 30
    assert isinstance(payload.get("effective_policy_profile"), dict)


def test_legacy_artifact_memory_entries_remain_readable(client, admin_auth_header):
    from agent.db_models import MemoryEntryDB
    from agent.repository import memory_entry_repo

    memory_entry_repo.save(
        MemoryEntryDB(
            task_id="LEGACY-MEM-1",
            title="Legacy summary",
            content="legacy content",
            memory_metadata={"structured_summary": {"changed_files": ["agent/routes/tasks.py"]}},
        )
    )

    items = [item.model_dump() for item in memory_entry_repo.get_by_task("LEGACY-MEM-1")]
    entry = next((item for item in items if item.get("task_id") == "LEGACY-MEM-1"), None)
    assert entry is not None
    assert entry.get("title") == "Legacy summary"
