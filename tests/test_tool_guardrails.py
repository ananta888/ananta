from agent.routes.tasks.utils import _update_local_task_status, _get_local_task_status
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
    calls = [{"name": "create_team", "args": {"name": "A", "team_type": "Scrum"}}, {"name": "create_team", "args": {"name": "B", "team_type": "Scrum"}}]
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
