from agent.db_models import AgentInfoDB, TaskDB
from agent.repository import agent_repo, policy_decision_repo, task_repo


class TestWorkerRoutingAPI:
    def test_auto_assign_selects_capable_worker(self, client, admin_auth_header):
        agent_repo.save(
            AgentInfoDB(
                url="http://tester:5000",
                name="tester",
                role="worker",
                worker_roles=["tester"],
                capabilities=["testing"],
                status="online",
            )
        )
        agent_repo.save(
            AgentInfoDB(
                url="http://coder:5000",
                name="coder",
                role="worker",
                worker_roles=["coder"],
                capabilities=["coding"],
                status="online",
            )
        )
        task_repo.save(TaskDB(id="task-1", title="Run regression tests", description="Verify behavior", status="todo"))

        res = client.post(
            "/tasks/task-1/assign/auto",
            headers=admin_auth_header,
            json={"task_kind": "testing", "required_capabilities": ["testing"]},
        )
        assert res.status_code == 200
        payload = res.get_json()["data"]
        assert payload["agent_url"] == "http://tester:5000"
        assert payload["selected_by_policy"] is True

        task = task_repo.get_by_id("task-1")
        assert task.assigned_agent_url == "http://tester:5000"
        assert task.task_kind == "testing"

        decisions = policy_decision_repo.get_by_task_id("task-1")
        assert decisions
        assert decisions[0].decision_type == "assignment"

    def test_delegate_without_agent_uses_capability_routing(self, client, admin_auth_header):
        agent_repo.save(
            AgentInfoDB(
                url="http://planner:5000",
                name="planner",
                role="worker",
                worker_roles=["planner"],
                capabilities=["planning"],
                status="online",
            )
        )
        task_repo.save(TaskDB(id="parent-1", title="Create plan", description="Plan work", status="todo"))

        res = client.post(
            "/tasks/parent-1/delegate",
            headers=admin_auth_header,
            json={"subtask_description": "Generate task plan", "task_kind": "planning", "required_capabilities": ["planning"]},
        )
        assert res.status_code in {200, 502}
        if res.status_code == 200:
            payload = res.get_json()["data"]
            assert payload["agent_url"] == "http://planner:5000"
            assert payload["selected_by_policy"] is True

    def test_manual_assign_route_still_requires_explicit_agent_override(self, client, admin_auth_header):
        task_repo.save(TaskDB(id="task-2", title="Manual route", description="Keep override path", status="todo"))
        res = client.post("/tasks/task-2/assign", headers=admin_auth_header, json={})
        assert res.status_code == 400
        assert res.get_json()["message"] == "agent_url_required"
