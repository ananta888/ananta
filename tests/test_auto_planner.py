"""
Tests for Auto-Planner and Trigger-System.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from agent.routes.tasks.auto_planner import (
    AutoPlanner,
    _parse_subtasks_from_llm_response,
    _build_planning_prompt,
    _build_followup_prompt,
)
from agent.routes.tasks.triggers import TriggerEngine


class TestAutoPlannerParsing:
    def test_parse_json_array(self):
        response = '[{"title": "Task 1", "description": "Desc 1", "priority": "High"}]'
        result = _parse_subtasks_from_llm_response(response)
        assert len(result) == 1
        assert result[0]["title"] == "Task 1"
        assert result[0]["priority"] == "High"

    def test_parse_json_with_markdown(self):
        response = '```json\n[{"title": "Task 1"}]\n```'
        result = _parse_subtasks_from_llm_response(response)
        assert len(result) == 1
        assert result[0]["title"] == "Task 1"

    def test_parse_list_format(self):
        response = """
        - Erste Aufgabe
        - Zweite Aufgabe
        * Dritte Aufgabe
        1. Vierte Aufgabe
        """
        result = _parse_subtasks_from_llm_response(response)
        assert len(result) == 4
        assert "Erste" in result[0]["description"]

    def test_parse_empty(self):
        assert _parse_subtasks_from_llm_response("") == []
        assert _parse_subtasks_from_llm_response("   ") == []


class TestAutoPlannerPrompts:
    def test_planning_prompt_contains_goal(self):
        prompt = _build_planning_prompt("Implementiere Login")
        assert "Implementiere Login" in prompt
        assert "ZIEL:" in prompt
        assert "JSON" in prompt

    def test_planning_prompt_with_context(self):
        prompt = _build_planning_prompt("Test Goal", context="Verwende Flask")
        assert "Verwende Flask" in prompt
        assert "KONTEXT:" in prompt

    def test_followup_prompt_contains_task_info(self):
        task = {"title": "Test Task", "description": "Test Description"}
        prompt = _build_followup_prompt(task, "Output here", 0)
        assert "Test Task" in prompt
        assert "erfolgreich" in prompt
        assert "Output here" in prompt

    def test_followup_prompt_shows_failure(self):
        task = {"title": "Failed Task"}
        prompt = _build_followup_prompt(task, "Error output", 1)
        assert "fehlgeschlagen" in prompt
        assert "exit code: 1" in prompt


class TestAutoPlanner:
    def test_configure(self):
        planner = AutoPlanner()
        result = planner.configure(
            enabled=True,
            auto_followup_enabled=False,
            max_subtasks_per_goal=5,
            default_priority="High",
        )
        assert planner.enabled is True
        assert planner.auto_followup_enabled is False
        assert planner.max_subtasks_per_goal == 5
        assert planner.default_priority == "High"

    def test_configure_bounds(self):
        planner = AutoPlanner()
        planner.configure(max_subtasks_per_goal=100, llm_timeout=1000)
        assert planner.max_subtasks_per_goal == 20
        assert planner.llm_timeout == 120

    def test_status(self):
        planner = AutoPlanner()
        planner.configure(enabled=True)
        status = planner.status()
        assert status["enabled"] is True
        assert "stats" in status

    def test_plan_goal_empty(self):
        planner = AutoPlanner()
        result = planner.plan_goal("")
        assert result.get("error") == "goal_required"

    def test_plan_goal_creates_tasks(self, app, monkeypatch):
        mock_response = json.dumps(
            [
                {"title": "Task 1", "description": "Desc 1", "priority": "High"},
                {"title": "Task 2", "description": "Desc 2", "priority": "Medium"},
            ]
        )

        monkeypatch.setattr(
            "agent.routes.tasks.auto_planner.generate_text",
            lambda prompt, provider=None, model=None, base_url=None, api_key=None, timeout=30: mock_response,
        )
        monkeypatch.setattr("agent.routes.tasks.auto_planner.config_repo", MagicMock(save=MagicMock()))

        planner = AutoPlanner()
        planner.configure(auto_start_autopilot=False)

        with app.app_context():
            result = planner.plan_goal("Test Goal", create_tasks=True)

        assert len(result["created_task_ids"]) == 2
        assert result.get("error") is None


class TestTriggerEngine:
    def test_enable_disable_source(self):
        engine = TriggerEngine()
        engine.enable_source("github")
        assert engine.is_source_enabled("github") is True
        engine.enable_source("github", False)
        assert engine.is_source_enabled("github") is False

    def test_webhook_signature_validation(self):
        engine = TriggerEngine()
        engine.set_webhook_secret("github", "my-secret")

        payload = b'{"test": "data"}'
        valid_sig = "sha256=" + __import__("hmac").new(b"my-secret", payload, __import__("hashlib").sha256).hexdigest()

        assert engine.verify_webhook_signature("github", payload, valid_sig) is True
        assert engine.verify_webhook_signature("github", payload, "sha256=invalid") is False

    def test_process_disabled_source(self):
        engine = TriggerEngine()
        result = engine.process_webhook("disabled_source", {"title": "Test"})
        assert result["status"] == "disabled"

    def test_default_handler_single_task(self):
        engine = TriggerEngine()
        tasks = engine._default_handler(
            "generic",
            {
                "title": "Bug Report",
                "description": "Something is broken",
            },
        )
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Bug Report"

    def test_default_handler_task_list(self):
        engine = TriggerEngine()
        tasks = engine._default_handler(
            "generic",
            {
                "tasks": [
                    {"title": "Task 1"},
                    {"title": "Task 2"},
                ]
            },
        )
        assert len(tasks) == 2

    def test_github_issue_handler(self):
        engine = TriggerEngine()
        tasks = engine._handle_github_issue(
            {
                "action": "opened",
                "issue": {
                    "number": 42,
                    "title": "Bug in login",
                    "body": "Login doesn't work",
                    "html_url": "https://github.com/org/repo/issues/42",
                    "labels": [{"name": "bug"}],
                },
                "repository": {"full_name": "org/repo"},
            }
        )
        assert len(tasks) == 1
        assert "GitHub Issue" in tasks[0]["title"]
        assert tasks[0]["priority"] == "High"

    def test_github_pr_handler(self):
        engine = TriggerEngine()
        tasks = engine._handle_github_pr(
            {
                "action": "opened",
                "pull_request": {
                    "number": 10,
                    "title": "Add feature",
                    "body": "Description here",
                    "html_url": "https://github.com/org/repo/pull/10",
                },
                "repository": {"full_name": "org/repo"},
            }
        )
        assert len(tasks) == 1
        assert "GitHub PR" in tasks[0]["title"]


class TestAutoPlannerAPI:
    @pytest.fixture
    def client(self, app):
        app.config["TESTING"] = True
        return app.test_client()

    @pytest.fixture
    def auth_headers(self, app):
        with app.app_context():
            from agent.auth import create_token

            token = create_token({"sub": "admin", "role": "admin"})
            return {"Authorization": f"Bearer {token}"}

    def test_status_endpoint(self, client, auth_headers):
        res = client.get("/tasks/auto-planner/status", headers=auth_headers)
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "success"
        assert "enabled" in data["data"]

    def test_configure_endpoint(self, client, auth_headers):
        res = client.post(
            "/tasks/auto-planner/configure", headers=auth_headers, json={"enabled": True, "max_subtasks_per_goal": 5}
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["data"]["enabled"] is True
        assert data["data"]["max_subtasks_per_goal"] == 5


class TestTriggersAPI:
    @pytest.fixture
    def client(self, app):
        app.config["TESTING"] = True
        return app.test_client()

    @pytest.fixture
    def auth_headers(self, app):
        with app.app_context():
            from agent.auth import create_token

            token = create_token({"sub": "admin", "role": "admin"})
            return {"Authorization": f"Bearer {token}"}

    def test_triggers_status(self, client, auth_headers):
        res = client.get("/triggers/status", headers=auth_headers)
        assert res.status_code == 200
        data = res.get_json()
        assert "enabled_sources" in data["data"]

    def test_test_trigger_endpoint(self, client, auth_headers):
        res = client.post(
            "/triggers/test",
            headers=auth_headers,
            json={"source": "generic", "payload": {"title": "Test", "description": "Test desc"}},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["data"]["would_create"] == 1

    def test_webhook_endpoint_disabled_source(self, client):
        from agent.routes.tasks.triggers import trigger_engine

        trigger_engine.enable_source("test_disabled", False)

        res = client.post("/triggers/webhook/test_disabled", json={"title": "Test"})
        assert res.status_code == 403

    def test_webhook_endpoint_creates_task(self, client, app, monkeypatch):
        from agent.routes.tasks.triggers import trigger_engine

        trigger_engine.enable_source("test_create", True)

        res = client.post("/triggers/webhook/test_create", json={"title": "New Task", "description": "From webhook"})
        assert res.status_code == 200
        data = res.get_json()
        assert data["data"]["tasks_created"] == 1
        assert len(data["data"]["task_ids"]) == 1
