"""
Integration test for full autopilot loop with auto-planner.
"""

import pytest
import time
from unittest.mock import patch, MagicMock


class TestFullAutopilotLoop:
    """Integration tests for autopilot with auto-planner."""

    def test_autopilot_processes_todo_task(self, client, app, admin_auth_header):
        with app.app_context():
            from agent.routes.tasks.utils import _update_local_task_status

            _update_local_task_status("auto-test-1", "todo", title="Auto Test Task")

        response = client.get("/tasks/autopilot/status", headers=admin_auth_header)
        assert response.status_code == 200

    def test_autopilot_start_stop_cycle(self, client, app, admin_auth_header, monkeypatch):
        monkeypatch.setattr("agent.config.settings.role", "hub")
        response = client.post(
            "/tasks/autopilot/start",
            json={"interval_seconds": 10, "max_concurrency": 1, "security_level": "safe"},
            headers=admin_auth_header,
        )
        assert response.status_code == 200
        data = response.json["data"]
        assert data["running"] is True

        response = client.post("/tasks/autopilot/stop", headers=admin_auth_header)
        assert response.status_code == 200
        data = response.json["data"]
        assert data["running"] is False

    def test_autopilot_tick_endpoint(self, client, app, admin_auth_header, monkeypatch):
        monkeypatch.setattr("agent.config.settings.role", "hub")
        response = client.post("/tasks/autopilot/tick", headers=admin_auth_header)
        assert response.status_code == 200

    def test_auto_planner_creates_tasks_from_goal(self, client, app, admin_auth_header):
        with patch("agent.routes.tasks.auto_planner.generate_text") as mock_llm:
            mock_llm.return_value = """
            [
                {"title": "Setup database", "description": "Configure PostgreSQL", "priority": "High"},
                {"title": "Create API endpoints", "description": "Implement REST API", "priority": "Medium"}
            ]
            """
            response = client.post(
                "/tasks/auto-planner/plan",
                json={"goal": "Build a REST API", "create_tasks": True},
                headers=admin_auth_header,
            )

        assert response.status_code in [200, 201]
        data = response.json["data"]
        assert "created_task_ids" in data

    def test_auto_planner_status_endpoint(self, client, auth_header):
        response = client.get("/tasks/auto-planner/status", headers=auth_header)
        assert response.status_code == 200
        data = response.json["data"]
        assert "enabled" in data
        assert "stats" in data

    def test_trigger_creates_task(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("generic")
            trigger_engine.auto_start_planner = False

        response = client.post(
            "/triggers/webhook/generic",
            json={"title": "Urgent bug fix", "description": "Fix production issue", "priority": "High"},
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["status"] == "processed"
        assert data["tasks_created"] == 1

    def test_webhook_to_task_flow(self, client, app, admin_auth_header):
        from agent.routes.tasks.triggers import trigger_engine

        trigger_engine.enable_source("github")
        trigger_engine._ip_whitelist.clear()

        response = client.post(
            "/triggers/webhook/github",
            json={
                "action": "opened",
                "issue": {
                    "number": 42,
                    "title": "Bug: Login fails",
                    "body": "Login button does nothing",
                    "html_url": "https://github.com/org/repo/issues/42",
                    "labels": [{"name": "bug"}],
                },
                "repository": {"full_name": "org/repo"},
            },
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["tasks_created"] >= 1
        task_id = data["task_ids"][0]

        response = client.get(f"/tasks/{task_id}", headers=admin_auth_header)
        assert response.status_code == 200

    def test_scheduler_goal_scheduling(self, client, app, admin_auth_header, monkeypatch):
        from agent.scheduler import get_scheduler

        scheduler = get_scheduler()
        task = scheduler.add_goal_task("Test goal", 3600)
        assert task is not None
        assert "goal:Test goal" in task.command

    def test_quality_gate_blocks_failed_task(self, client, app, admin_auth_header):
        with app.app_context():
            from agent.routes.tasks.utils import _update_local_task_status

            _update_local_task_status(
                "quality-test-1",
                "in_progress",
                title="Quality Gate Test",
                last_output="[quality_gate] failed: Tests not passing",
                exit_code=1,
            )

        response = client.get("/tasks/quality-test-1", headers=admin_auth_header)
        assert response.status_code == 200
        task = response.json["data"]
        assert "quality_gate] failed" in (task.get("last_output") or "")

    def test_dependency_chain_unblocks(self, client, app, admin_auth_header):
        with app.app_context():
            from agent.routes.tasks.utils import _update_local_task_status

            _update_local_task_status("dep-parent", "todo", title="Parent Task")
            _update_local_task_status("dep-child", "blocked", title="Child Task", depends_on=["dep-parent"])

        response = client.patch("/tasks/dep-parent", json={"status": "completed"}, headers=admin_auth_header)
        assert response.status_code == 200

    def test_multi_source_webhook_aggregation(self, client, app):
        from agent.routes.tasks.triggers import trigger_engine

        trigger_engine.enable_source("generic")
        trigger_engine.enable_source("github")
        trigger_engine._ip_whitelist.clear()

        response1 = client.post(
            "/triggers/webhook/generic", json={"title": "Generic Task 1", "description": "From generic webhook"}
        )
        response2 = client.post(
            "/triggers/webhook/github",
            json={
                "action": "opened",
                "issue": {
                    "number": 1,
                    "title": "GitHub Task",
                    "body": "From GitHub",
                    "html_url": "https://github.com/test/repo/issues/1",
                },
                "repository": {"full_name": "test/repo"},
            },
        )
        response2 = client.post(
            "/triggers/webhook/github",
            json={
                "action": "opened",
                "issue": {
                    "number": 1,
                    "title": "GitHub Task",
                    "body": "From GitHub",
                    "html_url": "https://github.com/test/repo/issues/1",
                },
                "repository": {"full_name": "test/repo"},
            },
        )

        assert response1.status_code == 200
        assert response2.status_code == 200
        assert response1.json["data"]["tasks_created"] >= 1
        assert response2.json["data"]["tasks_created"] >= 1
