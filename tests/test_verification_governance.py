import time

import jwt

from agent.config import settings
from agent.db_models import AgentInfoDB, GoalDB, TaskDB
from agent.repository import audit_repo, goal_repo, policy_decision_repo, task_repo, verification_record_repo
from agent.services.verification_service import get_verification_service


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
        assert records[0].results.get("failure_classification") == "execution_failure"
        assert records[0].results.get("repair_workflow", {}).get("next_action") == "escalate_to_human"

    def test_verification_failure_classification_external_gate(self):
        task_repo.save(TaskDB(id="vg-task-2b", title="Implement API", description="write code", status="assigned", task_kind="coding"))
        record = get_verification_service().create_or_update_record(
            "vg-task-2b",
            trace_id="tr-vg-2b",
            output="pytest passed",
            exit_code=0,
            gate_results={"passed": False},
        )
        assert record is not None
        assert record.status == "failed"
        assert record.results.get("failure_classification") == "external_gate_failure"
        assert record.results.get("repair_workflow", {}).get("next_action") == "fix_external_checks"

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
        assert payload["policy"]["total"] >= 1

    def test_non_admin_governance_summary_is_sanitized(self, client, admin_auth_header):
        goal = goal_repo.save(GoalDB(goal="Review secure flow", summary="Review secure flow", status="planned", team_id="team-a"))
        task_repo.save(TaskDB(id="vg-goal-task-2", title="Task", status="assigned", goal_id=goal.id, goal_trace_id=goal.trace_id, task_kind="coding"))
        client.post(
            "/tasks/orchestration/complete",
            headers=admin_auth_header,
            json={"task_id": "vg-goal-task-2", "actor": "http://coder:5000", "gate_results": {"passed": True}, "output": "ok", "trace_id": goal.trace_id},
        )
        token = jwt.encode(
            {
                "sub": "team-user",
                "role": "user",
                "team_id": "team-a",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            settings.secret_key,
            algorithm="HS256",
        )
        res = client.get(f"/goals/{goal.id}/governance-summary", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        payload = res.get_json()["data"]
        assert "latest" not in payload["policy"]
        assert payload["summary"]["governance_visible"] is False

    def test_audit_log_records_hash_chain_for_goal_workflow(self, client, admin_auth_header):
        create_res = client.post("/goals", headers=admin_auth_header, json={"goal": "Audit secure release"})
        goal_id = create_res.get_json()["data"]["goal"]["id"]
        node_id = create_res.get_json()["data"]["plan_node_ids"][0]

        client.patch(
            f"/goals/{goal_id}/plan/nodes/{node_id}",
            headers=admin_auth_header,
            json={"title": "Harden release checklist"},
        )

        logs = audit_repo.get_all(limit=20)
        assert any(log.action == "goal_created" and log.record_hash for log in logs)
        assert any(log.action == "plan_node_updated" and log.prev_hash for log in logs if log.action == "plan_node_updated")

    def test_policy_decision_inherits_goal_trace_when_not_explicitly_passed(self):
        goal = goal_repo.save(GoalDB(goal="Trace linkage", summary="Trace linkage", status="planned"))
        task_repo.save(TaskDB(id="vg-goal-task-3", title="Task", status="todo", goal_id=goal.id, goal_trace_id=goal.trace_id, task_kind="coding"))

        from agent.routes.tasks.orchestration_policy import persist_policy_decision

        decision = persist_policy_decision(
            decision_type="assignment",
            status="approved",
            policy_name="worker_assignment_policy",
            policy_version="assignment-v1",
            task_id="vg-goal-task-3",
        )
        assert decision.trace_id == goal.trace_id
        assert decision.goal_id == goal.id
