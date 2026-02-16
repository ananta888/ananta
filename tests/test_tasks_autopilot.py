import time

from agent.config import settings
from agent.db_models import AgentInfoDB, TaskDB
from agent.repository import agent_repo, task_repo
from agent.routes.tasks.autopilot import autonomous_loop
from agent.routes.tasks.quality_gates import evaluate_quality_gates
from agent.routes.tasks.utils import _update_local_task_status


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
        assert "circuit_breakers" in start_res.json["data"]

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
    assert res.json["data"]["reason"] == "no_available_workers"
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


def test_autopilot_guardrail_stops_on_dispatched_limit(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "autonomous_guardrails": {
            "enabled": True,
            "max_runtime_seconds": 9999,
            "max_ticks_total": 9999,
            "max_dispatched_total": 1,
        },
    }
    autonomous_loop.running = True
    autonomous_loop.dispatched_count = 1
    autonomous_loop.tick_count = 0
    autonomous_loop.started_at = time.time()

    with app.app_context():
        res = autonomous_loop.tick_once()

    assert res["dispatched"] == 0
    assert res["reason"] == "guardrail_max_dispatched_total_exceeded"
    assert autonomous_loop.running is False


def test_autopilot_retries_transient_worker_failure(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "autonomous_resilience": {
            "retry_attempts": 2,
            "retry_backoff_seconds": 0,
            "circuit_breaker_threshold": 5,
            "circuit_breaker_open_seconds": 30,
        },
    }
    autonomous_loop._worker_failure_streak = {}
    autonomous_loop._worker_circuit_open_until = {}
    task_repo.save(TaskDB(id="retry-1", title="Retry Task", status="todo"))
    agent_repo.save(AgentInfoDB(url="http://worker-r:5001", name="worker-r", role="worker", token="tok", status="online"))

    calls = {"propose": 0}

    def _fake_forward(worker_url, endpoint, data, token=None):
        if endpoint.endswith("/step/propose"):
            calls["propose"] += 1
            if calls["propose"] == 1:
                raise RuntimeError("transient")
            return {"status": "success", "data": {"reason": "ok", "command": "echo ok"}}
        return {"status": "success", "data": {"status": "completed", "exit_code": 0, "output": "execution success ok"}}

    monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _fake_forward)
    with app.app_context():
        res = autonomous_loop.tick_once()
    updated = task_repo.get_by_id("retry-1")
    assert res["dispatched"] == 1
    assert calls["propose"] == 2
    assert updated is not None and updated.status == "completed"


def test_autopilot_opens_circuit_breaker_after_threshold(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "autonomous_resilience": {
            "retry_attempts": 1,
            "retry_backoff_seconds": 0,
            "circuit_breaker_threshold": 1,
            "circuit_breaker_open_seconds": 60,
        },
    }
    autonomous_loop._worker_failure_streak = {}
    autonomous_loop._worker_circuit_open_until = {}
    task_repo.save(TaskDB(id="cb-1", title="CB Task 1", status="todo"))
    task_repo.save(TaskDB(id="cb-2", title="CB Task 2", status="todo"))
    agent_repo.save(AgentInfoDB(url="http://worker-cb:5001", name="worker-cb", role="worker", token="tok", status="online"))

    def _always_fail(*args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _always_fail)
    with app.app_context():
        first = autonomous_loop.tick_once()
        second = autonomous_loop.tick_once()
    assert first["reason"] == "ok"
    assert second["reason"] == "no_available_workers"
    assert "http://worker-cb:5001" in autonomous_loop._worker_circuit_open_until


def test_autopilot_unblocks_task_only_when_all_dependencies_completed(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "autonomous_resilience": {
            "retry_attempts": 1,
            "retry_backoff_seconds": 0,
            "circuit_breaker_threshold": 3,
            "circuit_breaker_open_seconds": 30,
        },
    }
    autonomous_loop._worker_failure_streak = {}
    autonomous_loop._worker_circuit_open_until = {}
    task_repo.save(TaskDB(id="dep-a", title="A", status="completed"))
    task_repo.save(TaskDB(id="dep-b", title="B", status="todo"))
    task_repo.save(TaskDB(id="dep-c", title="C", status="blocked", depends_on=["dep-a", "dep-b"]))

    with app.app_context():
        r1 = autonomous_loop.tick_once()
        c1 = task_repo.get_by_id("dep-c")
        _update_local_task_status("dep-b", "completed")
        r2 = autonomous_loop.tick_once()
        c2 = task_repo.get_by_id("dep-c")

    assert r1["reason"] in {"no_available_workers", "ok", "no_candidates"}
    assert c1 is not None and c1.status == "blocked"
    assert r2["reason"] in {"no_available_workers", "ok", "no_candidates"}
    assert c2 is not None and c2.status in {"todo", "assigned", "completed", "failed"}


def test_autopilot_start_persists_scope_fields(client, app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_TOKEN"] = "secret-token"
    headers = _auth_headers(app)
    try:
        res = client.post(
            "/tasks/autopilot/start",
            json={
                "interval_seconds": 9,
                "max_concurrency": 2,
                "goal": "ship sprint goal",
                "team_id": "team-42",
                "budget_label": "10k tokens",
                "security_level": "balanced",
            },
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json["data"]
        assert data["goal"] == "ship sprint goal"
        assert data["team_id"] == "team-42"
        assert data["budget_label"] == "10k tokens"
        assert data["security_level"] == "balanced"
        assert data["effective_security_policy"]["level"] == "balanced"
    finally:
        autonomous_loop.stop(persist=False)


def test_autopilot_team_scope_only_dispatches_matching_team(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "autonomous_resilience": {
            "retry_attempts": 1,
            "retry_backoff_seconds": 0,
            "circuit_breaker_threshold": 3,
            "circuit_breaker_open_seconds": 30,
        },
        "quality_gates": {
            "enabled": False,
            "autopilot_enforce": False,
        },
    }
    autonomous_loop._worker_failure_streak = {}
    autonomous_loop._worker_circuit_open_until = {}
    autonomous_loop.team_id = "team-a"
    task_repo.save(TaskDB(id="scope-a", title="A", status="todo", team_id="team-a"))
    task_repo.save(TaskDB(id="scope-b", title="B", status="todo", team_id="team-b"))
    agent_repo.save(AgentInfoDB(url="http://worker-s:5001", name="worker-s", role="worker", token="tok", status="online"))

    def _fake_forward(worker_url, endpoint, data, token=None):
        if endpoint.endswith("/step/propose"):
            return {"status": "success", "data": {"reason": "ok", "command": "echo ok"}}
        return {"status": "success", "data": {"status": "completed", "exit_code": 0, "output": "ok success"}}

    monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _fake_forward)
    try:
        with app.app_context():
            res = autonomous_loop.tick_once()
    finally:
        autonomous_loop.team_id = ""
    a = task_repo.get_by_id("scope-a")
    b = task_repo.get_by_id("scope-b")
    assert res["dispatched"] == 1
    assert a is not None and a.status in {"completed", "failed"}
    assert b is not None and b.status == "todo"


def test_autopilot_unwraps_nested_data_response(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "quality_gates": {"enabled": False, "autopilot_enforce": False},
    }
    task_repo.save(TaskDB(id="wrap-1", title="Wrap Task", status="todo"))
    agent_repo.save(AgentInfoDB(url="http://worker-wrap:5001", name="worker-wrap", role="worker", token="tok", status="online"))

    def _fake_forward(worker_url, endpoint, data, token=None):
        if endpoint.endswith("/step/propose"):
            return {"status": "success", "data": {"data": {"reason": "ok", "command": "echo ok"}}}
        return {"status": "success", "data": {"data": {"status": "completed", "exit_code": 0, "output": "ok"}}}

    monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _fake_forward)
    with app.app_context():
        res = autonomous_loop.tick_once()
        updated = task_repo.get_by_id("wrap-1")
    assert res["reason"] == "ok"
    assert updated is not None and updated.status == "completed"


def test_autopilot_circuit_status_endpoint(client, app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_TOKEN"] = "secret-token"
    headers = _auth_headers(app)
    autonomous_loop._worker_failure_streak = {"http://worker-cs:5001": 2}
    autonomous_loop._worker_circuit_open_until = {"http://worker-cs:5001": time.time() + 30}

    res = client.get("/tasks/autopilot/circuits", headers=headers)
    assert res.status_code == 200
    data = res.json["data"]
    assert data["open_count"] >= 1
    assert any(item["worker_url"] == "http://worker-cs:5001" for item in data["open_workers"])


def test_autopilot_circuit_reset_endpoint(client, app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_TOKEN"] = "secret-token"
    headers = _auth_headers(app)
    autonomous_loop._worker_failure_streak = {"http://worker-rs:5001": 3}
    autonomous_loop._worker_circuit_open_until = {"http://worker-rs:5001": time.time() + 30}

    res = client.post("/tasks/autopilot/circuits/reset", json={"worker_url": "http://worker-rs:5001"}, headers=headers)
    assert res.status_code == 200
    assert res.json["data"]["reset"] == 1
    cb = res.json["data"]["circuit_breakers"]
    assert not any(item["worker_url"] == "http://worker-rs:5001" for item in cb["open_workers"])


def test_autopilot_security_level_safe_blocks_write_tool_calls(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "quality_gates": {"enabled": False, "autopilot_enforce": False},
        "llm_tool_guardrails": {
            "enabled": True,
            "tool_classes": {"create_team": "write"},
            "class_cost_units": {"read": 1, "write": 5, "admin": 8, "unknown": 3},
        },
    }
    old_level = autonomous_loop.security_level
    autonomous_loop.security_level = "safe"
    task_repo.save(TaskDB(id="sec-safe-1", title="Safe policy task", status="todo"))
    agent_repo.save(AgentInfoDB(url="http://worker-safe:5001", name="worker-safe", role="worker", token="tok", status="online"))

    def _fake_forward(worker_url, endpoint, data, token=None):
        if endpoint.endswith("/step/propose"):
            return {"status": "success", "data": {"reason": "try tool", "tool_calls": [{"name": "create_team", "args": {"name": "X", "team_type": "Scrum"}}]}}
        return {"status": "success", "data": {"status": "completed", "exit_code": 0, "output": "ok"}}

    monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _fake_forward)
    try:
        with app.app_context():
            res = autonomous_loop.tick_once()
            updated = task_repo.get_by_id("sec-safe-1")
    finally:
        autonomous_loop.security_level = old_level
    assert res["reason"] == "ok"
    assert updated is not None and updated.status == "failed"
    assert any(
        (h.get("event_type") == "autopilot_security_policy_blocked")
        for h in (updated.history or [])
    )
