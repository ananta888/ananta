from agent.db_models import AgentInfoDB, TaskDB
from agent.config import settings
from agent.repository import agent_repo, policy_decision_repo, task_repo
from agent.routes.tasks.orchestration_policy import choose_worker_for_task


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
        assert payload["worker_selection"]["strategy"] == "capability_quality_load_match"
        assert payload["worker_selection"]["matched_capabilities"] == ["testing"]

        task = task_repo.get_by_id("task-1")
        assert task.assigned_agent_url == "http://tester:5000"
        assert task.task_kind == "testing"

        decisions = policy_decision_repo.get_by_task_id("task-1")
        assert decisions
        assert decisions[0].decision_type == "assignment"

    def test_delegate_without_agent_uses_capability_routing(self, client, admin_auth_header, monkeypatch):
        monkeypatch.setattr(settings, "role", "hub")
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
            assert payload["worker_selection"]["task_kind"] == "planning"
            assert payload["worker_selection"]["matched_roles"] == ["planner"]

    def test_manual_assign_route_still_requires_explicit_agent_override(self, client, admin_auth_header):
        task_repo.save(TaskDB(id="task-2", title="Manual route", description="Keep override path", status="todo"))
        res = client.post("/tasks/task-2/assign", headers=admin_auth_header, json={})
        assert res.status_code == 400
        assert res.get_json()["message"] == "agent_url_required"

    def test_worker_selection_uses_fallback_without_worker_to_worker_routing(self):
        selection = choose_worker_for_task(
            {"id": "task-3", "title": "Review code"},
            [{"url": "http://fallback:5000", "status": "online", "capabilities": [], "worker_roles": []}],
            task_kind="review",
            required_capabilities=["review"],
        )
        assert selection.worker_url == "http://fallback:5000"
        assert selection.strategy == "fallback"
        assert any(reason.startswith("fallback:") for reason in selection.reasons)
