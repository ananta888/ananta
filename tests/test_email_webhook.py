"""
Test for email webhook handler.
"""

import pytest


class TestEmailWebhook:
    """Tests for email webhook endpoint."""

    def test_email_webhook_creates_task(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("email")
            trigger_engine._ip_whitelist.clear()

        response = client.post(
            "/triggers/webhook/email",
            json={
                "from": "user@example.com",
                "subject": "Bug Report: Application crashes",
                "body": "The application crashes when I click the submit button.",
            },
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["status"] == "processed"
        assert data["tasks_created"] == 1
        assert "email" in data["task_ids"][0] or data["task_ids"][0].startswith("trg-")

    def test_email_webhook_with_html_body(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("email")
            trigger_engine._ip_whitelist.clear()

        response = client.post(
            "/triggers/webhook/email",
            json={
                "from": "admin@company.org",
                "subject": "Scheduled maintenance",
                "html": "<p>Maintenance scheduled for <b>Sunday</b>.</p>",
            },
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["tasks_created"] == 1

    def test_email_webhook_priority_detection_urgent(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("email")
            trigger_engine._ip_whitelist.clear()

        response = client.post(
            "/triggers/webhook/email",
            json={
                "from": "support@example.com",
                "subject": "URGENT: Production down",
                "body": "Please help immediately",
            },
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["tasks_created"] == 1

    def test_email_webhook_priority_detection_low(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("email")
            trigger_engine._ip_whitelist.clear()

        response = client.post(
            "/triggers/webhook/email",
            json={
                "from": "newsletter@company.com",
                "subject": "FYI: Weekly newsletter",
                "body": "Here is our weekly update",
            },
        )

        assert response.status_code == 200
        data = response.json["data"]
        assert data["tasks_created"] == 1

    def test_email_webhook_no_subject_skipped(self, client, app):
        with app.app_context():
            from agent.routes.tasks.triggers import trigger_engine

            trigger_engine.enable_source("email")
            trigger_engine._ip_whitelist.clear()

        response = client.post("/triggers/webhook/email", json={"from": "user@example.com", "body": "No subject here"})

        assert response.status_code == 200
        data = response.json["data"]
        assert data["tasks_created"] == 0
