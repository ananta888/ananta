from __future__ import annotations

import hashlib
import hmac
import json

from agent.services.repository_registry import get_repository_registry


def _github_signature(secret: str, payload_raw: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_raw, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _configure_review_webhooks(app, *, secret: str = "webhook-secret") -> None:
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG", {}) or {})
        cfg["pr_review_webhooks"] = {
            "test_mode": True,
            "allowed_providers": ["github", "gitlab"],
            "allowed_events": ["pull_request", "merge_request"],
            "allowed_repositories": ["org/repo"],
            "secrets": {"github": secret, "gitlab": "gitlab-secret"},
        }
        app.config["AGENT_CONFIG"] = cfg


def test_git_webhook_receiver_rejects_invalid_signature(client, app) -> None:
    _configure_review_webhooks(app)
    payload = {
        "action": "opened",
        "repository": {"full_name": "org/repo"},
        "pull_request": {"number": 17, "title": "Test", "html_url": "https://example.invalid/pr/17"},
    }
    payload_raw = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/webhooks/git/github?test_mode=1",
        data=payload_raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=invalid",
        },
    )
    assert response.status_code == 401
    assert response.get_json()["message"] == "invalid_signature"


def test_git_webhook_receiver_rejects_unsupported_event(client, app) -> None:
    secret = "webhook-secret"
    _configure_review_webhooks(app, secret=secret)
    payload = {
        "action": "opened",
        "repository": {"full_name": "org/repo"},
        "pull_request": {"number": 18, "title": "Test", "html_url": "https://example.invalid/pr/18"},
    }
    payload_raw = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/webhooks/git/github?test_mode=1",
        data=payload_raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": _github_signature(secret, payload_raw),
        },
    )
    assert response.status_code == 422
    assert response.get_json()["message"] == "unsupported_event_type"


def test_git_webhook_receiver_accepts_allowed_pull_request_event_and_queues_task(client, app) -> None:
    secret = "webhook-secret"
    _configure_review_webhooks(app, secret=secret)
    payload = {
        "action": "opened",
        "repository": {"full_name": "org/repo"},
        "pull_request": {"number": 19, "title": "Feature", "html_url": "https://example.invalid/pr/19"},
    }
    payload_raw = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/webhooks/git/github?test_mode=1",
        data=payload_raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _github_signature(secret, payload_raw),
            "X-GitHub-Delivery": "delivery-19",
        },
    )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["status"] == "queued"
    assert data["execution"] == "queued_only"
    assert data["provider"] == "github"
    assert data["repository"] == "org/repo"
    task_id = data["task_id"]

    with app.app_context():
        task = get_repository_registry().task_repo.get_by_id(task_id)
        assert task is not None
        assert "PR review request org/repo#19" in str(task.title or "")

