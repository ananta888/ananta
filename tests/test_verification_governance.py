from agent.db_models import AgentInfoDB, GoalDB, TaskDB
from agent.repository import goal_repo, policy_decision_repo, task_repo, verification_record_repo


class TestVerificationGovernance:
    def test_manual_assign_blocks_incompatible_worker(self, client, admin_auth_header):
        from agent.repository import agent_repo

        agent_repo.save(
            AgentInfoDB(
                url="http://reviewer:5000",
                name="reviewer",
                role="worker",
                worker_roles=["reviewer"],
                capabilities=["review"],
                status="online",
            )
        )
        task_repo.save(TaskDB(id="vg-assign-1", title="Run tests", status="todo", task_kind="testing"))

        res = client.post(
            "/tasks/vg-assign-1/assign",
            headers=admin_auth_header,
            json={"agent_url": "http://reviewer:5000", "task_kind": "testing", "required_capabilities": ["testing"]},
        )
        assert res.status_code == 409
        decisions = policy_decision_repo.get_by_task_id("vg-assign-1")
        assert decisions
        assert decisions[0].status == "blocked"

    def test_task_complete_creates_verification_record(self, client, admin_auth_header):
        task_repo.save(TaskDB(id="vg-task-1", title="Implement API", description="write code", status="assigned", task_kind="coding"))
        res = client.post(
            "/tasks/orchestration/complete",
            headers=admin_auth_header,
            json={"task_id": "vg-task-1", "actor": "http://coder:5000", "gate_results": {"passed": True}, "output": "pytest passed", "trace_id": "tr-vg-1"},
        )
        assert res.status_code == 200
        payload = res.get_json()["data"]
        assert payload["verification_status"]["status"] == "passed"

        records = verification_record_repo.get_by_task_id("vg-task-1")
        assert records
        assert records[0].status == "passed"

    def test_failed_verification_retries_then_escalates(self, client, admin_auth_header):
        task_repo.save(TaskDB(id="vg-task-2", title="Implement API", description="write code", status="assigned", task_kind="coding"))
        for _ in range(3):
            client.post(
                "/tasks/orchestration/complete",
                headers=admin_auth_header,
                json={"task_id": "vg-task-2", "actor": "http://coder:5000", "gate_results": {"passed": False}, "output": "no evidence", "trace_id": "tr-vg-2"},
            )

        records = verification_record_repo.get_by_task_id("vg-task-2")
        assert records
        assert records[0].status == "escalated"
        assert records[0].escalation_reason == "verification_retry_limit_reached"

    def test_task_verification_endpoint_returns_spec_and_status(self, client, admin_auth_header):
        task_repo.save(TaskDB(id="vg-task-3", title="Review docs", status="todo", task_kind="review"))
        res = client.get("/tasks/vg-task-3/verification", headers=admin_auth_header)
        assert res.status_code == 200
        payload = res.get_json()["data"]
        assert payload["verification_spec"]["policy"] is True

    def test_goal_governance_summary_aggregates_policy_and_verification(self, client, admin_auth_header):
        goal = goal_repo.save(GoalDB(goal="Ship feature", summary="Ship feature", status="planned"))
        task_repo.save(TaskDB(id="vg-goal-task", title="Task", status="assigned", goal_id=goal.id, goal_trace_id=goal.trace_id, task_kind="coding"))
        client.post(
            "/tasks/orchestration/complete",
            headers=admin_auth_header,
            json={"task_id": "vg-goal-task", "actor": "http://coder:5000", "gate_results": {"passed": True}, "output": "pytest passed", "trace_id": goal.trace_id},
        )
        policy_decisions = policy_decision_repo.get_by_task_id("vg-goal-task")
        if not policy_decisions:
            from agent.routes.tasks.orchestration_policy import persist_policy_decision

            persist_policy_decision(
                decision_type="assignment",
                status="approved",
                policy_name="worker_assignment_policy",
                policy_version="assignment-v1",
                reasons=["manual_override_allowed"],
                task_id="vg-goal-task",
                goal_id=goal.id,
                trace_id=goal.trace_id,
            )

        res = client.get(f"/goals/{goal.id}/governance-summary", headers=admin_auth_header)
        assert res.status_code == 200
        payload = res.get_json()["data"]
        assert payload["verification"]["total"] >= 1
        assert payload["summary"]["governance_visible"] is True
