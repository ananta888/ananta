from __future__ import annotations

import hashlib
import hmac
import json

from flask import Flask

from agent.routes.webhooks import webhooks_bp


def _github_signature(secret: str, payload_raw: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_raw, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class _QueueSpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def ingest_task(self, **kwargs) -> None:
        self.calls.append(kwargs)


class _CoreServicesStub:
    def __init__(self, queue_spy: _QueueSpy) -> None:
        self.task_queue_service = queue_spy


def _build_client(monkeypatch, *, secret: str = "webhook-secret"):
    queue_spy = _QueueSpy()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["AGENT_CONFIG"] = {
        "pr_review_webhooks": {
            "test_mode": True,
            "allowed_providers": ["github", "gitlab"],
            "allowed_events": ["pull_request", "merge_request"],
            "allowed_repositories": ["org/repo"],
            "secrets": {"github": secret, "gitlab": "gitlab-secret"},
        }
    }
    monkeypatch.setattr("agent.routes.webhooks.get_core_services", lambda: _CoreServicesStub(queue_spy))
    monkeypatch.setattr("agent.routes.webhooks.log_audit", lambda *_args, **_kwargs: None)
    app.register_blueprint(webhooks_bp)
    return app.test_client(), queue_spy


def test_git_webhook_receiver_rejects_invalid_signature(monkeypatch) -> None:
    client, _queue_spy = _build_client(monkeypatch)
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


def test_git_webhook_receiver_rejects_unsupported_event(monkeypatch) -> None:
    secret = "webhook-secret"
    client, _queue_spy = _build_client(monkeypatch, secret=secret)
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


def test_git_webhook_receiver_accepts_allowed_pull_request_event_and_queues_task(monkeypatch) -> None:
    secret = "webhook-secret"
    client, queue_spy = _build_client(monkeypatch, secret=secret)
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
    assert len(queue_spy.calls) == 1
    queued = queue_spy.calls[0]
    assert queued["task_id"] == data["task_id"]
    assert queued["source"] == "git_webhook_receiver"
    assert queued["event_type"] == "pr_review_requested"
