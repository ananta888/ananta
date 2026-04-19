from agent.db_models import AgentInfoDB, GoalDB, TaskDB
from agent.repository import agent_repo, goal_repo, policy_decision_repo, task_repo, verification_record_repo


def test_goal_task_delegation_completion_backend_flow_stays_consistent(client, admin_auth_header, monkeypatch):
    monkeypatch.setattr(
        "agent.routes.tasks.auto_planner.generate_text",
        lambda **_kwargs: '[{"title":"Implement backend flow","description":"Wire task completion","priority":"High"}]',
    )
    create = client.post("/goals", headers=admin_auth_header, json={"goal": "Ship backend flow"})
    assert create.status_code == 201
    created = create.get_json()["data"]
    goal = created["goal"]
    task_id = created["created_task_ids"][0]

    claim = client.post(
        "/tasks/orchestration/claim",
        headers=admin_auth_header,
        json={"task_id": task_id, "agent_url": "http://alpha:5001", "lease_seconds": 60, "idempotency_key": "core-flow"},
    )
    assert claim.status_code == 200
    assert claim.get_json()["data"]["claimed"] is True

    complete = client.post(
        "/tasks/orchestration/complete",
        headers=admin_auth_header,
        json={
            "task_id": task_id,
            "actor": "http://alpha:5001",
            "gate_results": {"passed": True},
            "output": "Implemented backend flow. Tests passed.",
            "trace_id": goal["trace_id"],
        },
    )
    assert complete.status_code == 200
    assert complete.get_json()["data"]["status"] == "completed"

    task = task_repo.get_by_id(task_id)
    assert task is not None
    assert task.goal_id == goal["id"]
    assert task.goal_trace_id == goal["trace_id"]
    assert task.status == "completed"
    assert any(event.get("event_type") == "task_completed_with_gates" for event in (task.history or []))
    assert verification_record_repo.get_by_task_id(task_id)[0].status == "passed"

    detail = client.get(f"/goals/{goal['id']}/detail", headers=admin_auth_header)
    assert detail.status_code == 200
    detail_payload = detail.get_json()["data"]
    assert task_id in detail_payload["trace"]["task_ids"]
    assert detail_payload["artifacts"]["result_summary"]["completed_tasks"] >= 1


def test_verification_failed_flow_records_retry_history_and_governance_summary(client, admin_auth_header):
    goal = goal_repo.save(GoalDB(goal="Fail closed", summary="Fail closed", status="planned"))
    task_repo.save(TaskDB(id="flow-fail-1", title="Fail verification", status="assigned", goal_id=goal.id, goal_trace_id=goal.trace_id, task_kind="coding"))

    response = client.post(
        "/tasks/orchestration/complete",
        headers=admin_auth_header,
        json={
            "task_id": "flow-fail-1",
            "actor": "http://coder:5000",
            "gate_results": {"passed": False},
            "output": "runtime error",
            "trace_id": goal.trace_id,
            "exit_code": 1,
        },
    )
    assert response.status_code == 200

    record = verification_record_repo.get_by_task_id("flow-fail-1")[0]
    assert record.status == "failed"
    assert record.retry_count == 1
    assert (record.results or {}).get("repair_workflow", {}).get("repair_required") is True
    task = task_repo.get_by_id("flow-fail-1")
    assert any(event.get("event_type") == "task_verification_updated" for event in (task.history or []))

    summary = client.get(f"/goals/{goal.id}/governance-summary", headers=admin_auth_header)
    assert summary.status_code == 200
    payload = summary.get_json()["data"]
    assert payload["verification"]["failed"] >= 1
    assert payload["verification"]["latest_events"][0]["channel"] == "governance"


def test_policy_and_exposure_boundaries_are_enforced_through_real_api_paths(client, app, admin_auth_header):
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG", {}) or {})
        cfg["exposure_policy"] = {"openai_compat": {"enabled": False, "allow_user_auth": False}}
        app.config["AGENT_CONFIG"] = cfg

    blocked = client.get("/v1/ananta/capabilities", headers=admin_auth_header)
    assert blocked.status_code == 403
    assert blocked.get_json()["data"]["details"] == "openai_compat_disabled"

    invalid = client.post(
        "/config",
        headers=admin_auth_header,
        json={"terminal_policy": {"allowed_roles": "ops"}},
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["message"] == "invalid_terminal_allowed_roles"

    read_model = client.get("/governance/policy", headers=admin_auth_header)
    assert read_model.status_code == 200
    governance = read_model.get_json()["data"]
    assert "exposure_policy" in governance
    assert "terminal_policy" in governance
    assert governance["decisions"]["openai_compat"]["allowed"] is False


def test_read_model_contracts_expose_stable_backend_shapes(client, admin_auth_header):
    task_repo.save(TaskDB(id="contract-task-1", title="Contract task", description="shape", status="todo", task_kind="analysis"))

    assistant = client.get("/assistant/read-model", headers=admin_auth_header)
    dashboard = client.get("/dashboard/read-model?include_task_snapshot=1", headers=admin_auth_header)
    orchestration = client.get("/tasks/orchestration/read-model", headers=admin_auth_header)

    assert assistant.status_code == 200
    assert dashboard.status_code == 200
    assert orchestration.status_code == 200

    assistant_data = assistant.get_json()["data"]
    dashboard_data = dashboard.get_json()["data"]
    orchestration_data = orchestration.get_json()["data"]
    assert {"config", "settings", "assistant_capabilities"}.issubset(assistant_data)
    assert "governance" in ((assistant_data.get("settings") or {}).get("summary") or {})
    assert {"runtime_profile", "operations", "task_snapshot"}.issubset(dashboard_data)
    assert {"queue", "recent_tasks", "artifact_flow", "worker_execution_reconciliation"}.issubset(orchestration_data)
    assert any(item.get("id") == "contract-task-1" for item in orchestration_data["recent_tasks"])


def test_multi_worker_routing_prefers_capable_worker_and_reports_no_worker_case(client, admin_auth_header):
    agent_repo.save(
        AgentInfoDB(
            url="http://offline-tester:5000",
            name="offline-tester",
            role="worker",
            worker_roles=["tester"],
            capabilities=["testing"],
            status="offline",
        )
    )
    agent_repo.save(
        AgentInfoDB(
            url="http://online-coder:5000",
            name="online-coder",
            role="worker",
            worker_roles=["coder"],
            capabilities=["coding"],
            status="online",
        )
    )
    agent_repo.save(
        AgentInfoDB(
            url="http://online-tester:5000",
            name="online-tester",
            role="worker",
            worker_roles=["tester"],
            capabilities=["testing"],
            status="online",
        )
    )
    task_repo.save(TaskDB(id="multi-worker-1", title="Run tests", description="routing", status="todo"))

    assigned = client.post(
        "/tasks/multi-worker-1/assign/auto",
        headers=admin_auth_header,
        json={"task_kind": "testing", "required_capabilities": ["testing"]},
    )
    assert assigned.status_code == 200
    payload = assigned.get_json()["data"]
    assert payload["agent_url"] == "http://online-tester:5000"
    assert payload["worker_selection"]["matched_capabilities"] == ["testing"]
    assert payload["selected_by_policy"] is True

    decisions = policy_decision_repo.get_by_task_id("multi-worker-1")
    assert decisions and decisions[0].status == "approved"


def test_multi_worker_routing_reports_no_worker_when_directory_has_no_online_candidates(client, admin_auth_header):
    agent_repo.save(
        AgentInfoDB(
            url="http://offline-security:5000",
            name="offline-security",
            role="worker",
            worker_roles=["security"],
            capabilities=["security"],
            status="offline",
        )
    )
    task_repo.save(TaskDB(id="multi-worker-none", title="Need security", description="routing", status="todo"))

    no_worker = client.post(
        "/tasks/multi-worker-none/assign/auto",
        headers=admin_auth_header,
        json={"task_kind": "security", "required_capabilities": ["security"]},
    )

    assert no_worker.status_code == 409
    assert no_worker.get_json()["message"] == "no_worker_available"
    assert no_worker.get_json()["data"]["reasons"] == ["no_online_worker_available"]
