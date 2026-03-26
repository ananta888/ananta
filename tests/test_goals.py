import time

import jwt

from agent.config import settings
from agent.repository import goal_repo, task_repo


class TestGoalsAPI:
    def test_goal_readiness_exposes_defaults(self, client, admin_auth_header):
        res = client.get("/goals/readiness", headers=admin_auth_header)
        assert res.status_code == 200
        data = res.get_json()["data"]
        assert "defaults" in data
        assert data["defaults"]["planning"]["engine"] == "auto_planner"
        assert "happy_path_ready" in data

    def test_create_goal_simple_flow_persists_goal_and_task_links(self, client, admin_auth_header):
        res = client.post("/goals", headers=admin_auth_header, json={"goal": "Implement login feature"})
        assert res.status_code == 201
        payload = res.get_json()["data"]
        goal = payload["goal"]

        assert goal["status"] == "planned"
        assert payload["created_task_ids"]
        assert payload["plan_id"]
        assert payload["plan_node_ids"]
        assert payload["workflow"]["provenance"]["planning.create_tasks"] == "default"

        persisted_goal = goal_repo.get_by_id(goal["id"])
        assert persisted_goal is not None
        assert persisted_goal.trace_id

        linked_tasks = task_repo.get_by_goal_id(goal["id"])
        assert linked_tasks
        assert all(task.goal_trace_id == persisted_goal.trace_id for task in linked_tasks)
        assert all(task.plan_id == payload["plan_id"] for task in linked_tasks)

    def test_create_goal_advanced_overrides_preserve_provenance(self, client, admin_auth_header):
        res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={
                "goal": "Implement reporting feature",
                "team_id": "team-advanced",
                "create_tasks": False,
                "use_repo_context": False,
                "constraints": ["No breaking API changes"],
                "acceptance_criteria": ["Result must be documented"],
                "execution_preferences": {"verification_mode": "strict"},
                "visibility": {"show_plan": True},
                "workflow": {"policy": {"security_level": "strict"}},
            },
        )
        assert res.status_code == 201
        payload = res.get_json()["data"]
        goal = payload["goal"]
        workflow = payload["workflow"]

        assert payload["created_task_ids"] == []
        assert workflow["effective"]["routing"]["team_id"] == "team-advanced"
        assert workflow["provenance"]["planning.create_tasks"] == "override"
        assert workflow["provenance"]["planning.use_repo_context"] == "override"
        assert workflow["provenance"]["policy.security_level"] == "override"

        persisted_goal = goal_repo.get_by_id(goal["id"])
        assert persisted_goal.constraints == ["No breaking API changes"]
        assert persisted_goal.acceptance_criteria == ["Result must be documented"]
        assert persisted_goal.visibility["show_plan"] is True

    def test_get_goal_returns_task_count(self, client, admin_auth_header):
        create_res = client.post("/goals", headers=admin_auth_header, json={"goal": "Create feature backlog"})
        goal_id = create_res.get_json()["data"]["goal"]["id"]

        res = client.get(f"/goals/{goal_id}", headers=admin_auth_header)
        assert res.status_code == 200
        payload = res.get_json()["data"]
        assert payload["id"] == goal_id
        assert payload["task_count"] >= 1

    def test_goal_plan_inspection_and_patch(self, client, admin_auth_header):
        create_res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={"goal": "Implement reporting feature", "create_tasks": False},
        )
        goal_payload = create_res.get_json()["data"]
        goal_id = goal_payload["goal"]["id"]
        plan_id = goal_payload["plan_id"]
        node_id = goal_payload["plan_node_ids"][0]

        get_res = client.get(f"/goals/{goal_id}/plan", headers=admin_auth_header)
        assert get_res.status_code == 200
        plan_payload = get_res.get_json()["data"]
        assert plan_payload["plan"]["id"] == plan_id
        assert plan_payload["nodes"]

        patch_res = client.patch(
            f"/goals/{goal_id}/plan/nodes/{node_id}",
            headers=admin_auth_header,
            json={"title": "Adjusted step", "priority": "High"},
        )
        assert patch_res.status_code == 200
        patched = patch_res.get_json()["data"]
        assert patched["title"] == "Adjusted step"
        assert patched["status"] == "edited"

    def test_non_admin_goal_access_is_team_scoped(self, client, admin_auth_header):
        open_res = client.post("/goals", headers=admin_auth_header, json={"goal": "Public goal"})
        scoped_res = client.post("/goals", headers=admin_auth_header, json={"goal": "Scoped goal", "team_id": "team-a"})

        token = jwt.encode(
            {
                "sub": "scoped-user",
                "role": "user",
                "team_id": "team-a",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            settings.secret_key,
            algorithm="HS256",
        )
        headers = {"Authorization": f"Bearer {token}"}

        list_res = client.get("/goals", headers=headers)
        assert list_res.status_code == 200
        goal_ids = {item["id"] for item in list_res.get_json()["data"]}
        assert open_res.get_json()["data"]["goal"]["id"] in goal_ids
        assert scoped_res.get_json()["data"]["goal"]["id"] in goal_ids

        other_token = jwt.encode(
            {
                "sub": "other-user",
                "role": "user",
                "team_id": "team-b",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            settings.secret_key,
            algorithm="HS256",
        )
        other_headers = {"Authorization": f"Bearer {other_token}"}
        forbidden_res = client.get(f"/goals/{scoped_res.get_json()['data']['goal']['id']}", headers=other_headers)
        assert forbidden_res.status_code == 404

    def test_goal_detail_exposes_artifact_first_summary(self, client, admin_auth_header):
        create_res = client.post("/goals", headers=admin_auth_header, json={"goal": "Deliver release"})
        goal_id = create_res.get_json()["data"]["goal"]["id"]
        task_id = create_res.get_json()["data"]["created_task_ids"][0]

        client.post(
            "/tasks/orchestration/complete",
            headers=admin_auth_header,
            json={"task_id": task_id, "actor": "http://coder:5000", "gate_results": {"passed": True}, "output": "Release notes ready", "trace_id": goal_repo.get_by_id(goal_id).trace_id},
        )

        detail_res = client.get(f"/goals/{goal_id}/detail", headers=admin_auth_header)
        assert detail_res.status_code == 200
        payload = detail_res.get_json()["data"]
        assert payload["artifacts"]["result_summary"]["task_count"] >= 1
        assert payload["artifacts"]["headline_artifact"]["preview"] == "Release notes ready"

    def test_goal_first_run_happy_path_uses_default_configuration(self, client, admin_auth_header):
        res = client.post("/goals", headers=admin_auth_header, json={"goal": "Bootstrap first run"})
        assert res.status_code == 201
        payload = res.get_json()["data"]
        assert payload["workflow"]["provenance"]["planning.create_tasks"] == "default"
        assert payload["workflow"]["effective"]["routing"]["mode"] == "active_team_or_hub_default"
        assert payload["readiness"]["happy_path_ready"] is True
        assert payload["plan_limits"]["max_nodes"] >= 1
        goal_id = payload["goal"]["id"]

        detail_res = client.get(f"/goals/{goal_id}/detail", headers=admin_auth_header)
        assert detail_res.status_code == 200
        detail = detail_res.get_json()["data"]
        assert detail["trace"]["trace_id"].startswith("goal-")
        assert detail["plan"]["plan"]["goal_id"] == goal_id
