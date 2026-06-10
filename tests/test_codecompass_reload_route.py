"""CCARI-011: Flask route tests for /api/codecompass/reload-context."""
from __future__ import annotations

import pytest

from agent.services import context_delivery_service
from agent.services.context_delivery_service import ContextDeliveryService


@pytest.fixture
def client(monkeypatch):
    from flask import Flask
    from agent.routes.codecompass_reload import codecompass_reload_bp

    app = Flask(__name__)
    app.register_blueprint(codecompass_reload_bp)

    # Stub the ContextDeliveryService method to return canned data.
    def fake_handle(self, *, task, request):
        return {
            "schema": "context_reload_response.v1",
            "status": "ok",
            "code": None,
            "delivered": [{"path": "x.java"}],
            "warnings": [],
        }

    def fake_blocked(self, *, task, request):
        return {
            "schema": "context_reload_response.v1",
            "status": "policy_blocked",
            "code": "policy_blocked",
            "delivered": [],
            "warnings": ["policy_blocked"],
        }

    monkeypatch.setattr(ContextDeliveryService, "handle_reload_request", fake_handle)

    class _StubTaskRepo:
        def get_by_id(self, task_id):
            class _T:
                id = "t1"
                def model_dump(self_inner):
                    return {"id": "t1", "prompt": "x"}
            return _T() if task_id == "t1" else None

    from agent.services import repository_registry

    class _StubRegistry:
        def __init__(self):
            self.task_repo = _StubTaskRepo()

    monkeypatch.setattr(
        repository_registry,
        "get_repository_registry",
        lambda: _StubRegistry(),
    )

    return app.test_client()


def test_reload_context_success(client):
    response = client.post(
        "/api/codecompass/reload-context",
        json={
            "task_id": "t1",
            "request": {
                "kind": "context_reload_request",
                "reason": "missing data",
                "risk": "read_only",
                "requested_context": [
                    {"type": "symbol", "query": "Foo"}
                ],
            },
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["delivered"] == [{"path": "x.java"}]


def test_reload_context_task_not_found(client, monkeypatch):
    response = client.post(
        "/api/codecompass/reload-context",
        json={
            "task_id": "does-not-exist",
            "request": {
                "kind": "context_reload_request",
                "reason": "x",
                "risk": "read_only",
                "requested_context": [{"type": "symbol", "query": "X"}],
            },
        },
    )
    assert response.status_code == 404


def test_reload_context_missing_task_id(client):
    response = client.post(
        "/api/codecompass/reload-context",
        json={"request": {"kind": "context_reload_request"}},
    )
    assert response.status_code == 400


def test_reload_context_missing_request(client):
    response = client.post(
        "/api/codecompass/reload-context",
        json={"task_id": "t1"},
    )
    assert response.status_code == 400


def test_reload_context_policy_blocked(client, monkeypatch):
    def fake_blocked(self, *, task, request):
        return {
            "schema": "context_reload_response.v1",
            "status": "policy_blocked",
            "code": "policy_blocked",
            "delivered": [],
            "warnings": ["policy_blocked"],
        }

    monkeypatch.setattr(ContextDeliveryService, "handle_reload_request", fake_blocked)
    response = client.post(
        "/api/codecompass/reload-context",
        json={
            "task_id": "t1",
            "request": {
                "kind": "context_reload_request",
                "reason": "delete things",
                "risk": "write",
                "requested_context": [{"type": "file_range", "path": "x.java", "start_line": 1, "end_line": 1}],
            },
        },
    )
    assert response.status_code == 409
    body = response.get_json()
    assert body["status"] == "policy_blocked"
