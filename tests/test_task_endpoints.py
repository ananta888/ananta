import pytest
from unittest.mock import patch, MagicMock
import json
import os

def test_task_specific_endpoints_path(client, app):
    """Verifiziert, dass die neuen Task-spezifischen Endpunkte erreichbar sind."""
    
    tid = "T-123456"
    
    # Wir müssen sicherstellen, dass der Task in der lokalen "Datenbank" existiert
    # Da wir in Tests oft In-Memory oder Mock-Pfade nutzen, schauen wir uns _update_local_task_status an.
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status
        _update_local_task_status(tid, "assigned", assigned_to="test-agent")

    # 1. Propose auf dem neuen Pfad
    with patch('agent.routes.tasks.execution.run_llm_cli_command') as mock_cli:
        mock_cli.return_value = (0, '{"reason": "Test", "command": "echo hello"}', "", "aider")
        response = client.post(f'/tasks/{tid}/step/propose', json={"prompt": "test"})
        assert response.status_code == 200
        assert response.json["data"]["command"] == "echo hello"
        assert response.json["data"]["backend"] == "aider"
        assert response.json["data"]["routing"]["effective_backend"] in {"aider", "sgpt", "opencode", "mistral_code"}
        with app.app_context():
            from agent.routes.tasks.utils import _get_local_task_status
            t = _get_local_task_status(tid)
            assert t is not None
            lp = t.get("last_proposal") or {}
            assert lp.get("backend") == "aider"
            assert isinstance(lp.get("cli_result", {}).get("latency_ms"), int)
            assert any((h.get("event_type") == "proposal_result") for h in (t.get("history") or []))

    # 2. Execute auf dem neuen Pfad
    with patch('agent.shell.PersistentShell.execute') as mock_exec:
        mock_exec.return_value = ("hello", 0)
        # Wir müssen ein last_proposal im Task haben, damit execute funktioniert
        with app.app_context():
            _update_local_task_status(tid, "proposing", last_proposal={"command": "echo hello", "reason": "Test"})
            
        response = client.post(f'/tasks/{tid}/step/execute', json={})
        assert response.status_code == 200
        assert response.json["data"]["output"] == "hello"
        with app.app_context():
            from agent.routes.tasks.utils import _get_local_task_status
            t = _get_local_task_status(tid)
            assert t is not None
            hist = t.get("history") or []
            assert any((h.get("event_type") == "execution_result") for h in hist)

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
        _update_local_task_status(
            "TL-SPB",
            "failed",
            team_id="team-a",
            assigned_agent_url="http://worker-1:5000",
            history=[
                {
                    "event_type": "autopilot_security_policy_blocked",
                    "timestamp": 14,
                    "blocked_reasons": ["guardrail_class_limit_exceeded:write"],
                    "blocked_tools": ["create_team"],
                    "security_level": "safe",
                }
            ],
        )
        _update_local_task_status(
            "TL-WF",
            "failed",
            team_id="team-a",
            assigned_agent_url="http://worker-1:5000",
            history=[
                {
                    "event_type": "autopilot_worker_failed",
                    "timestamp": 15,
                    "reason": "worker_forward_failed:http://worker-1:5000",
                }
            ],
        )
        _update_local_task_status("TL-2", "completed", team_id="team-b", history=[{"event_type": "autopilot_result", "timestamp": 12, "status": "completed"}])

    res = client.get("/tasks/timeline?team_id=team-a&error_only=1&limit=50")
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
    assert any(item["event_type"] == "autopilot_security_policy_blocked" for item in data["items"])
    assert any(item["event_type"] == "autopilot_worker_failed" for item in data["items"])
    assert all(item["event_type"] != "task_created" for item in data["items"])


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


def test_task_execute_auto_records_llm_benchmark(client, app, tmp_path):
    tid = "T-BENCH-AUTO"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        app.config["DATA_DIR"] = str(tmp_path)
        _update_local_task_status(tid, "assigned", description="Implement feature X")

    with patch("agent.routes.tasks.execution.run_llm_cli_command") as mock_cli:
        mock_cli.return_value = (0, '{"reason":"go","command":"echo ok"}', "", "aider")
        propose_res = client.post(f"/tasks/{tid}/step/propose", json={"prompt": "implement endpoint", "model": "gpt-4o-mini"})
        assert propose_res.status_code == 200

    with patch("agent.shell.PersistentShell.execute") as mock_exec:
        mock_exec.return_value = ("ok", 0)
        execute_res = client.post(f"/tasks/{tid}/step/execute", json={})
        assert execute_res.status_code == 200
        assert execute_res.json["data"]["status"] == "completed"

    bench_path = os.path.join(str(tmp_path), "llm_model_benchmarks.json")
    assert os.path.exists(bench_path)
    with open(bench_path, "r", encoding="utf-8") as fh:
        db = json.load(fh)

    model_entry = (db.get("models") or {}).get("aider:gpt-4o-mini")
    assert model_entry is not None
    coding_bucket = (model_entry.get("task_kinds") or {}).get("coding") or {}
    assert int(coding_bucket.get("total") or 0) >= 1


def test_task_execute_benchmark_fallback_uses_config_defaults(client, app, tmp_path):
    tid = "T-BENCH-FALLBACK"
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        app.config["DATA_DIR"] = str(tmp_path)
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["default_provider"] = "lmstudio"
        cfg["default_model"] = "model-fallback"
        cfg["llm_config"] = {"provider": "lmstudio", "model": "model-fallback"}
        app.config["AGENT_CONFIG"] = cfg
        _update_local_task_status(
            tid,
            "proposing",
            description="Document architecture",
            last_proposal={"command": "echo ok", "reason": "legacy proposal without model/backend"},
        )

    with patch("agent.shell.PersistentShell.execute") as mock_exec:
        mock_exec.return_value = ("ok", 0)
        execute_res = client.post(f"/tasks/{tid}/step/execute", json={})
        assert execute_res.status_code == 200
        assert execute_res.json["data"]["status"] == "completed"

    bench_path = os.path.join(str(tmp_path), "llm_model_benchmarks.json")
    with open(bench_path, "r", encoding="utf-8") as fh:
        db = json.load(fh)
    model_entry = (db.get("models") or {}).get("lmstudio:model-fallback")
    assert model_entry is not None
