from agent.config import settings
from agent.db_models import TaskDB
from agent.repository import task_repo
from agent.routes.tasks.autopilot import autonomous_loop


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
