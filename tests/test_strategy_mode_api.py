from unittest.mock import patch

from tests.fixtures.mock_openai_compatible_provider import make_mock_invoke_with_tools, make_mock_invoke_with_json_schema


TASK_ID = "T-STRATEGY-MODE-API-001"


def test_task_propose_accepts_strategy_mode_and_exposes_effective_mode(app, client, admin_auth_header, monkeypatch):
    monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
    # AGENT_CONFIG is built at app init from settings (DEFAULT_PROVIDER=mock in Docker);
    # patch it here so effective_config reflects "lmstudio" during the test.
    app.config["AGENT_CONFIG"]["default_provider"] = "lmstudio"
    tool_mock = make_mock_invoke_with_tools([
        {"name": "write_file", "args": {"path": "app.py", "content": "print(1)\n"}},
    ])
    monkeypatch.setattr(
        "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
        tool_mock,
    )

    create_res = client.post(
        "/tasks",
        json={
            "id": TASK_ID,
            "title": "Strategy mode task",
            "description": "Create minimal app",
            "task_kind": "new_software_project",
            "status": "assigned",
        },
        headers=admin_auth_header,
    )
    assert create_res.status_code in (200, 201)

    res = client.post(
        f"/tasks/{TASK_ID}/step/propose",
        json={"prompt": "Create app", "strategy_mode": "openai_compatible_tool_calling"},
        headers=admin_auth_header,
    )
    assert res.status_code == 200
    data = (res.json or {}).get("data") or {}
    meta = data.get("propose_strategy_meta") or {}
    assert meta.get("effective_strategy_mode") == "openai_compatible_tool_calling"
    assert meta.get("selected_strategy") in {"tool_calling_llm", "json_schema_llm", "flexible_llm_normalization"}
