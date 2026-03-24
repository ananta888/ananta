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
        assert payload["workflow"]["provenance"]["planning.create_tasks"] == "default"

        persisted_goal = goal_repo.get_by_id(goal["id"])
        assert persisted_goal is not None
        assert persisted_goal.trace_id

        linked_tasks = task_repo.get_by_goal_id(goal["id"])
        assert linked_tasks
        assert all(task.goal_trace_id == persisted_goal.trace_id for task in linked_tasks)

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
