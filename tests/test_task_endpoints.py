import pytest
from unittest.mock import patch, MagicMock
import json

def test_task_specific_endpoints_path(client, app):
    """Verifiziert, dass die neuen Task-spezifischen Endpunkte erreichbar sind."""
    
    tid = "T-123456"
    
    # Wir müssen sicherstellen, dass der Task in der lokalen "Datenbank" existiert
    # Da wir in Tests oft In-Memory oder Mock-Pfade nutzen, schauen wir uns _update_local_task_status an.
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status
        _update_local_task_status(tid, "assigned", assigned_to="test-agent")

    # 1. Propose auf dem neuen Pfad
    with patch('agent.routes.tasks.execution._call_llm') as mock_llm:
        mock_llm.return_value = '{"reason": "Test", "command": "echo hello"}'
        response = client.post(f'/tasks/{tid}/step/propose', json={"prompt": "test"})
        assert response.status_code == 200
        assert response.json["data"]["command"] == "echo hello"

    # 2. Execute auf dem neuen Pfad
    with patch('agent.shell.PersistentShell.execute') as mock_exec:
        mock_exec.return_value = ("hello", 0)
        # Wir müssen ein last_proposal im Task haben, damit execute funktioniert
        with app.app_context():
            _update_local_task_status(tid, "proposing", last_proposal={"command": "echo hello", "reason": "Test"})
            
        response = client.post(f'/tasks/{tid}/step/execute', json={})
        assert response.status_code == 200
        assert response.json["data"]["output"] == "hello"

def test_task_specific_endpoints_old_path_fail(client):
    """Verifiziert, dass die alten Pfade nicht mehr funktionieren (404)."""
    tid = "T-123456"
    
    response = client.post(f'/tasks/{tid}/propose', json={})
    assert response.status_code == 404
    
    response = client.post(f'/tasks/{tid}/execute', json={})
    assert response.status_code == 404

def test_task_unassign(client, app):
    """Verifiziert den Unassign-Endpunkt."""
    tid = "T-UNASSIGN"
    
    # 1. Task erstellen und zuweisen
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status, _get_local_task_status
        _update_local_task_status(tid, "assigned", assigned_agent_url="http://agent-1:5000")
        
        task = _get_local_task_status(tid)
        assert task["status"] == "assigned"
        assert task["assigned_agent_url"] == "http://agent-1:5000"

    # 2. Unassign aufrufen
    response = client.post(f'/tasks/{tid}/unassign')
    assert response.status_code == 200
    assert response.json["data"]["status"] == "todo"
    
    # 3. Status prüfen
    with app.app_context():
        task = _get_local_task_status(tid)
        assert task["status"] == "todo"
        # In JSON wird None zu null, was in Python wieder None ist oder der Key fehlt (falls wir ihn löschen würden)
        # _update_local_task_status nutzt .update(), also bleibt der Key mit Wert None
        assert task.get("assigned_agent_url") is None


def test_create_followups_deduplicates(client, app):
    tid = "T-FOLLOWUP"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status, _get_local_task_status
        _update_local_task_status(tid, "in_progress", description="parent")

    payload = {
        "items": [
            {"description": "Implement API endpoint", "priority": "High"},
            {"description": "Implement   API endpoint", "priority": "High"},
            {"description": "Write tests", "priority": "Medium"},
        ]
    }
    response = client.post(f"/tasks/{tid}/followups", json=payload)
    assert response.status_code == 200
    data = response.json["data"]
    assert len(data["created"]) == 2
    assert len(data["skipped"]) == 1
    assert data["skipped"][0]["reason"] == "duplicate"
    assert all(entry["status"] == "blocked" for entry in data["created"])

    with app.app_context():
        from agent.routes.tasks.utils import _get_local_task_status
        for entry in data["created"]:
            task = _get_local_task_status(entry["id"])
            assert task["parent_task_id"] == tid
            assert task["status"] == "blocked"


def test_autopilot_unblocks_child_when_parent_completed(app, monkeypatch):
    from agent.config import settings
    from agent.routes.tasks.utils import _update_local_task_status, _get_local_task_status
    from agent.routes.tasks.autopilot import autonomous_loop

    monkeypatch.setattr(settings, "role", "hub")
    with app.app_context():
        _update_local_task_status("PARENT-1", "completed", description="parent done")
        _update_local_task_status("CHILD-1", "blocked", description="child", parent_task_id="PARENT-1")
        res = autonomous_loop.tick_once()
        child = _get_local_task_status("CHILD-1")

    assert res["reason"] in {"ok", "no_online_workers", "no_available_workers", "no_candidates"}
    assert child["status"] in {"todo", "failed", "assigned", "completed"}
    assert child["status"] != "blocked"


def test_tasks_timeline_endpoint_filters_and_errors(client, app):
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status
        _update_local_task_status(
            "TL-1",
            "failed",
            team_id="team-a",
            assigned_agent_url="http://worker-1:5000",
            last_output="[quality_gate] failed: missing_coding_quality_markers",
            last_exit_code=1,
            history=[
                {"event_type": "autopilot_decision", "timestamp": 10, "reason": "because", "delegated_to": "http://worker-1:5000"},
                {"event_type": "autopilot_result", "timestamp": 11, "status": "failed", "exit_code": 1},
            ],
        )
        _update_local_task_status(
            "TL-GR",
            "failed",
            team_id="team-a",
            assigned_agent_url="http://worker-1:5000",
            history=[
                {
                    "event_type": "tool_guardrail_blocked",
                    "timestamp": 13,
                    "blocked_tools": ["create_team"],
                    "blocked_reasons": ["guardrail_class_limit_exceeded:write"],
                    "reason": "tool_guardrail_blocked",
                }
            ],
        )
        _update_local_task_status("TL-2", "completed", team_id="team-b", history=[{"event_type": "autopilot_result", "timestamp": 12, "status": "completed"}])

    res = client.get("/tasks/timeline?team_id=team-a&limit=50")
    assert res.status_code == 200
    data = res.json["data"]
    assert isinstance(data["items"], list)
    assert data["total"] >= 1
    assert all(item["team_id"] == "team-a" for item in data["items"])
    assert any(item["event_type"] in {"execution_result", "autopilot_result"} for item in data["items"])
    assert any(
        item["event_type"] == "tool_guardrail_blocked"
        and "guardrail_class_limit_exceeded:write" in (item.get("details", {}).get("blocked_reasons") or [])
        for item in data["items"]
    )


def test_task_dependencies_cycle_rejected(client, app):
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status
        _update_local_task_status("D-A", "todo")
        _update_local_task_status("D-B", "todo", depends_on=["D-A"])

    res = client.patch("/tasks/D-A", json={"depends_on": ["D-B"]})
    assert res.status_code == 400
    assert res.json["message"] == "dependency_cycle_detected"


def test_task_propose_forwarding_unwraps_nested_data(client, app):
    tid = "T-FWD-PROPOSE"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status
        _update_local_task_status(
            tid,
            "assigned",
            assigned_agent_url="http://worker-x:5001",
            assigned_agent_token="tok",
            description="forward test",
        )

    with patch("agent.routes.tasks.execution._forward_to_worker") as mock_fwd:
        mock_fwd.return_value = {"status": "success", "data": {"data": {"command": "echo hi", "reason": "ok"}}}
        res = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "x"})
        assert res.status_code == 200
        assert res.json["data"]["command"] == "echo hi"


def test_task_execute_forwarding_unwraps_nested_data(client, app):
    tid = "T-FWD-EXEC"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status, _get_local_task_status
        _update_local_task_status(
            tid,
            "assigned",
            assigned_agent_url="http://worker-y:5001",
            assigned_agent_token="tok",
            description="forward exec",
        )
        before = _get_local_task_status(tid)
        assert before is not None

    with patch("agent.routes.tasks.execution._forward_to_worker") as mock_fwd:
        mock_fwd.return_value = {"status": "success", "data": {"data": {"status": "completed", "output": "ok", "exit_code": 0}}}
        res = client.post(f"/tasks/{tid}/step/execute", json={"command": "echo hi"})
        assert res.status_code == 200
        assert res.json["data"]["status"] == "completed"
