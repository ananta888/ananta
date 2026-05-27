from __future__ import annotations

from flask import Flask

from agent.bootstrap.audit_middleware import register_audit_middleware


def test_audit_middleware_emits_request_start_and_complete(monkeypatch) -> None:
    captured: list[dict] = []

    class _FakeAuditService:
        def emit(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr("agent.bootstrap.audit_middleware.get_execution_audit_service", lambda: _FakeAuditService())
    monkeypatch.setattr("agent.bootstrap.audit_middleware.get_correlation_id", lambda: "corr-123")
    app = Flask(__name__)
    register_audit_middleware(app)

    @app.route("/api/ping", methods=["GET"])
    def _ping():
        return {"ok": True}

    client = app.test_client()
    response = client.get("/api/ping", headers={"X-Correlation-ID": "corr-123"})
    assert response.status_code == 200
    assert len(captured) == 2
    assert captured[0]["operation_type"] == "http_request_started"
    assert captured[1]["operation_type"] == "http_request_completed"
    assert captured[0]["trace_id"] == "corr-123"
