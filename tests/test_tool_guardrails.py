from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status
from agent.tool_guardrails import evaluate_tool_call_guardrails


def test_tool_guardrails_blocks_by_class_and_cost():
    cfg = {
        "llm_tool_guardrails": {
            "enabled": True,
            "max_tool_calls_per_request": 10,
            "max_external_calls_per_request": 1,
            "max_estimated_cost_units_per_request": 6,
            "class_limits": {"write": 1},
            "class_cost_units": {"read": 1, "write": 5, "admin": 8, "unknown": 3},
            "tool_classes": {"create_team": "write"},
            "max_tokens_per_request": 1,
        }
    }
    calls = [
        {"name": "create_team", "args": {"name": "A", "team_type": "Scrum"}},
        {"name": "create_team", "args": {"name": "B", "team_type": "Scrum"}},
    ]
    decision = evaluate_tool_call_guardrails(calls, cfg, token_usage={"estimated_total_tokens": 20})
    assert decision.allowed is False
    assert "guardrail_class_limit_exceeded:write" in decision.reasons
    assert "guardrail_max_external_calls_exceeded" in decision.reasons
    assert "guardrail_max_estimated_cost_exceeded" in decision.reasons
    assert "guardrail_max_estimated_tokens_exceeded" in decision.reasons


def test_task_execute_blocks_tool_calls_by_guardrails(client, app):
    with app.app_context():
        token = app.config.get("AGENT_TOKEN")
        app.config["AGENT_CONFIG"]["llm_tool_guardrails"] = {
            "enabled": True,
            "max_tool_calls_per_request": 5,
            "max_external_calls_per_request": 1,
            "max_estimated_cost_units_per_request": 10,
            "class_limits": {"write": 1},
            "class_cost_units": {"write": 5, "unknown": 3},
            "external_classes": ["write"],
            "tool_classes": {"create_team": "write"},
        }
        _update_local_task_status("TG-1", "todo", description="guard test")

    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "tool_calls": [
            {"name": "create_team", "args": {"name": "A", "team_type": "Scrum"}},
            {"name": "create_team", "args": {"name": "B", "team_type": "Scrum"}},
        ]
    }
    res = client.post("/tasks/TG-1/step/execute", json=payload, headers=headers)
    assert res.status_code == 400
    assert res.json["message"] == "tool_guardrail_blocked"

    with app.app_context():
        task = _get_local_task_status("TG-1")
        assert task is not None
        assert task["status"] == "failed"
        history = task.get("history") or []
        assert history
        latest = history[-1]
        assert latest.get("event_type") == "tool_guardrail_blocked"
        assert "guardrail_class_limit_exceeded:write" in (latest.get("blocked_reasons") or [])


def test_step_execute_with_task_id_persists_guardrail_block_history(client, app):
    with app.app_context():
        token = app.config.get("AGENT_TOKEN")
        app.config["AGENT_CONFIG"]["llm_tool_guardrails"] = {
            "enabled": True,
            "max_tool_calls_per_request": 10,
            "max_external_calls_per_request": 10,
            "max_estimated_cost_units_per_request": 999,
            "max_tokens_per_request": 1,
        }
        _update_local_task_status("TG-2", "todo", description="guard test generic step")

    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "task_id": "TG-2",
        "tool_calls": [{"name": "create_team", "args": {"name": "A", "team_type": "Scrum"}}],
    }
    res = client.post("/step/execute", json=payload, headers=headers)
    assert res.status_code == 400
    assert res.json["message"] == "tool_guardrail_blocked"

    with app.app_context():
        task = _get_local_task_status("TG-2")
        assert task is not None
        assert task["status"] == "failed"
        history = task.get("history") or []
        assert history
        latest = history[-1]
        assert latest.get("event_type") == "tool_guardrail_blocked"
        assert "guardrail_max_estimated_tokens_exceeded" in (latest.get("blocked_reasons") or [])


def test_task_execute_blocks_tool_calls_outside_task_scope(client, app):
    with app.app_context():
        token = app.config.get("AGENT_TOKEN")
        _update_local_task_status(
            "TG-SCOPE-1",
            "todo",
            description="scope guard test",
            worker_execution_context={"allowed_tools": ["list_teams"]},
        )

    headers = {"Authorization": f"Bearer {token}"}
    payload = {"tool_calls": [{"name": "create_team", "args": {"name": "A", "team_type": "Scrum"}}]}
    res = client.post("/tasks/TG-SCOPE-1/step/execute", json=payload, headers=headers)
    assert res.status_code == 400
    assert res.json["message"] == "tool_guardrail_blocked"

    details = ((res.json.get("data") or {}).get("details")) or {}
    assert details.get("blocked_reasons_by_tool", {}).get("create_team") == "tool_not_allowed_for_task_scope"

    with app.app_context():
        task = _get_local_task_status("TG-SCOPE-1")
        assert task is not None
        latest = (task.get("history") or [])[-1]
        assert latest.get("reason") == "tool_scope_blocked"
        assert "tool_not_allowed_for_task_scope" in (latest.get("blocked_reasons") or [])


def test_task_execute_blocks_scoped_terminal_command_without_terminal_capability(client, app):
    with app.app_context():
        token = app.config.get("AGENT_TOKEN")
        app.config["AGENT_CONFIG"]["execution_risk_policy"] = {
            "enabled": True,
            "default_action": "deny",
            "task_scoped_only": True,
            "require_terminal_capability_for_command": True,
            "deny_risk_levels": ["high", "critical"],
        }
        _update_local_task_status(
            "TG-RISK-1",
            "todo",
            description="risk guard test",
            worker_execution_context={"allowed_tools": ["list_teams"]},
            required_capabilities=[],
        )

    headers = {"Authorization": f"Bearer {token}"}
    payload = {"command": "rm -rf /tmp/risky"}
    res = client.post("/tasks/TG-RISK-1/step/execute", json=payload, headers=headers)
    assert res.status_code == 400
    assert res.json["message"] == "tool_guardrail_blocked"
    details = ((res.json.get("data") or {}).get("details")) or {}
    assert "execution_risk_denied" in ",".join(details.get("blocked_reasons") or [])
