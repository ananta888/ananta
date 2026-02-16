from agent.config import settings
from agent.db_models import AgentInfoDB, TaskDB
from agent.repository import agent_repo, task_repo
from agent.routes.tasks.autopilot import autonomous_loop
from agent.routes.tasks.quality_gates import evaluate_quality_gates


def _auth_headers(app):
    return {"Authorization": f"Bearer {app.config.get('AGENT_TOKEN')}"}


def test_autopilot_start_status_stop(client, app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_TOKEN"] = "secret-token"
    headers = _auth_headers(app)

    try:
        start_res = client.post(
            "/tasks/autopilot/start",
            json={"interval_seconds": 7, "max_concurrency": 1},
            headers=headers,
        )
        assert start_res.status_code == 200
        assert start_res.json["data"]["running"] is True
        assert start_res.json["data"]["interval_seconds"] == 7
        assert start_res.json["data"]["max_concurrency"] == 1

        status_res = client.get("/tasks/autopilot/status", headers=headers)
        assert status_res.status_code == 200
        assert status_res.json["data"]["running"] is True

        stop_res = client.post("/tasks/autopilot/stop", headers=headers)
        assert stop_res.status_code == 200
        assert stop_res.json["data"]["running"] is False
    finally:
        autonomous_loop.stop(persist=False)


def test_autopilot_tick_without_workers_marks_reason(client, app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_TOKEN"] = "secret-token"
    headers = _auth_headers(app)
    task_repo.save(TaskDB(id="auto-1", title="Autopilot Candidate", status="todo"))

    res = client.post("/tasks/autopilot/tick", headers=headers)
    assert res.status_code == 200
    assert res.json["data"]["reason"] == "no_online_workers"
    assert res.json["data"]["dispatched"] == 0


def test_quality_gate_fails_for_coding_task_without_markers():
    t = TaskDB(id="qg-1", title="Implement endpoint", description="Fix code path")
    ok, reason = evaluate_quality_gates(t, output="done", exit_code=0)
    assert ok is False
    assert reason in {"insufficient_output_evidence", "missing_coding_quality_markers"}


def test_autopilot_applies_quality_gate_on_completed_step(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "quality_gates": {
            "enabled": True,
            "autopilot_enforce": True,
            "coding_keywords": ["implement", "code"],
            "required_output_markers_for_coding": ["pytest passed"],
            "min_output_chars": 4,
        },
    }
    task_repo.save(TaskDB(id="qg-auto-1", title="Implement API", description="Write code", status="todo"))
    agent_repo.save(AgentInfoDB(url="http://worker-1:5001", name="worker-1", role="worker", token="tok", status="online"))

    responses = [
        {"status": "success", "data": {"reason": "run tests", "command": "pytest -q"}},
        {"status": "success", "data": {"status": "completed", "exit_code": 0, "output": "execution finished"}},
    ]

    def _fake_forward(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _fake_forward)
    res = autonomous_loop.tick_once()
    updated = task_repo.get_by_id("qg-auto-1")
    assert res["dispatched"] == 1
    assert updated is not None
    assert updated.status == "failed"
    assert "quality_gate" in (updated.last_output or "")
