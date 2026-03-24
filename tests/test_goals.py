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
