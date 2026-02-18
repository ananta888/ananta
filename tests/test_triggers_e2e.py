"""
E2E tests for Trigger webhook endpoints.
"""

import json
import pytest
from unittest.mock import patch, MagicMock


class TestWebhookEndpoints:
    """E2E tests for webhook receiving and processing."""

    def test_generic_webhook_creates_task(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("generic")

        response = client.post(
            "/triggers/webhook/generic",
            json={"title": "Test Task from Webhook", "description": "Created via webhook"},
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["status"] == "processed"
        assert data["tasks_created"] == 1
        assert len(data["task_ids"]) == 1

    def test_generic_webhook_with_tasks_array(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("generic")

        response = client.post(
            "/triggers/webhook/generic",
            json={
                "tasks": [
                    {"title": "Task 1", "description": "First task"},
                    {"title": "Task 2", "description": "Second task", "priority": "High"},
                ]
            },
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["status"] == "processed"
        assert data["tasks_created"] == 2

    def test_github_issue_webhook(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("github")

        response = client.post(
            "/triggers/webhook/github",
            json={
                "action": "opened",
                "issue": {
                    "number": 42,
                    "title": "Bug: Login fails",
                    "body": "The login button does nothing",
                    "html_url": "https://github.com/org/repo/issues/42",
                    "labels": [{"name": "bug"}],
                },
                "repository": {"full_name": "org/repo"},
            },
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["status"] == "processed"
        assert data["tasks_created"] == 1

    def test_github_pr_webhook(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("github")

        response = client.post(
            "/triggers/webhook/github",
            json={
                "action": "opened",
                "pull_request": {
                    "number": 10,
                    "title": "Feature: Add login",
                    "body": "Implements user login",
                    "html_url": "https://github.com/org/repo/pull/10",
                },
                "repository": {"full_name": "org/repo"},
            },
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["status"] == "processed"
        assert data["tasks_created"] == 1

    def test_slack_event_webhook(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("slack")

        response = client.post(
            "/triggers/webhook/slack",
            json={
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "text": "We need to fix the production bug",
                    "user": "U12345",
                    "channel": "C12345",
                },
            },
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["status"] == "processed"
        assert data["tasks_created"] == 1

    def test_jira_issue_webhook(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("jira")

        response = client.post(
            "/triggers/webhook/jira",
            json={
                "webhookEvent": "jira:issue_created",
                "issue": {
                    "key": "PROJ-123",
                    "fields": {
                        "summary": "Implement new feature",
                        "description": "Detailed description",
                        "priority": {"name": "High"},
                        "issuetype": {"name": "Story"},
                    },
                },
            },
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["status"] == "processed"
        assert data["tasks_created"] == 1

    def test_disabled_source_rejected(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("disabled_source", enabled=False)

        response = client.post(
            "/triggers/webhook/disabled_source",
            json={"title": "Should not work"},
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 403
        assert response.json["message"] == "source_disabled"

    def test_webhook_signature_validation(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("secured")
            trigger_engine.set_webhook_secret("secured", "my-secret")

        import hmac
        import hashlib

        payload = json.dumps({"title": "Test"}).encode()
        valid_sig = "sha256=" + hmac.new(b"my-secret", payload, hashlib.sha256).hexdigest()

        response = client.post(
            "/triggers/webhook/secured",
            data=payload,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": valid_sig},
        )

        assert response.status_code == 200
        assert response.json["data"]["status"] == "processed"

    def test_webhook_invalid_signature_rejected(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("secured2")
            trigger_engine.set_webhook_secret("secured2", "my-secret")

        response = client.post(
            "/triggers/webhook/secured2",
            data=json.dumps({"title": "Test"}).encode(),
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=invalidsignature"},
        )

        assert response.status_code == 401
        assert response.json["message"] == "invalid_signature"


class TestTriggerStatusEndpoints:
    """Tests for trigger status and configuration endpoints."""

    def test_triggers_status_authenticated(self, client, app, auth_header):
        response = client.get("/triggers/status", headers=auth_header)
        assert response.status_code == 200
        data = response.json["data"]
        assert "enabled_sources" in data
        assert "stats" in data

    def test_triggers_configure_admin(self, client, admin_auth_header):
        response = client.post(
            "/triggers/configure",
            json={
                "enabled_sources": ["generic", "github"],
                "ip_whitelists": {"github": ["192.168.1.0/24"]},
                "rate_limits": {"github": {"max_requests": 100, "window_seconds": 60}},
            },
            headers=admin_auth_header,
        )
        assert response.status_code == 200
        data = response.json["data"]
        assert "generic" in data["enabled_sources"]
        assert "github" in data["enabled_sources"]

    def test_triggers_test_endpoint(self, client, auth_header):
        response = client.post(
            "/triggers/test",
            json={"source": "generic", "payload": {"title": "Test Task", "description": "Only a test"}},
            headers=auth_header,
        )
        assert response.status_code == 200
        data = response.json["data"]
        assert data["would_create"] == 1
        assert len(data["parsed_tasks"]) == 1


class TestTriggerRateLimitingE2E:
    """E2E tests for rate limiting via HTTP."""

    def test_rate_limit_returns_429(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("rate_limited")
            trigger_engine.set_rate_limit("rate_limited", max_requests=1, window_seconds=60)

        response1 = client.post(
            "/triggers/webhook/rate_limited",
            json={"title": "First"},
            headers={"Content-Type": "application/json", "X-Forwarded-For": "10.0.0.1"},
        )
        assert response1.status_code == 200

        response2 = client.post(
            "/triggers/webhook/rate_limited",
            json={"title": "Second"},
            headers={"Content-Type": "application/json", "X-Forwarded-For": "10.0.0.1"},
        )
        assert response2.status_code == 429
        assert response2.json["message"] == "rate_limit_exceeded"


class TestTriggerIPWhitelistE2E:
    """E2E tests for IP whitelist via HTTP."""

    def test_ip_whitelist_blocks(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("whitelisted")
            trigger_engine.set_ip_whitelist("whitelisted", ["192.168.1.1"])

        response = client.post(
            "/triggers/webhook/whitelisted",
            json={"title": "Test"},
            headers={"Content-Type": "application/json", "X-Forwarded-For": "10.0.0.1"},
        )
        assert response.status_code == 403
        assert response.json["message"] == "ip_not_whitelisted"

    def test_ip_whitelist_allows(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("whitelisted2")
            trigger_engine.set_ip_whitelist("whitelisted2", ["192.168.1.1"])

        response = client.post(
            "/triggers/webhook/whitelisted2",
            json={"title": "Test"},
            headers={"Content-Type": "application/json", "X-Forwarded-For": "192.168.1.1"},
        )
        assert response.status_code == 200
