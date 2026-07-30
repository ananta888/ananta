from __future__ import annotations

from flask import Flask

from agent.bootstrap.audit_middleware import register_audit_middleware


def test_audit_middleware_emits_mutation_start_and_complete(monkeypatch) -> None:
    captured: list[dict] = []
    observed: list[dict] = []

    class _FakeAuditService:
        def emit(self, **kwargs):
            captured.append(kwargs)

    class _FakeMetrics:
        def observe(self, **kwargs):
            observed.append(kwargs)

    monkeypatch.setattr("agent.bootstrap.audit_middleware.get_execution_audit_service", lambda: _FakeAuditService())
    monkeypatch.setattr("agent.bootstrap.audit_middleware.get_correlation_id", lambda: "corr-123")
    monkeypatch.setattr("agent.bootstrap.audit_middleware.get_http_request_metrics", lambda: _FakeMetrics())
    app = Flask(__name__)
    register_audit_middleware(app)

    @app.route("/api/ping", methods=["POST"])
    def _ping():
        return {"ok": True}

    client = app.test_client()
    response = client.post("/api/ping", headers={"X-Correlation-ID": "corr-123"})
    assert response.status_code == 200
    assert len(captured) == 2
    assert observed == []
    assert captured[0]["operation_type"] == "http_request_started"
    assert captured[1]["operation_type"] == "http_request_completed"
    assert captured[0]["trace_id"] == "corr-123"


def test_audit_middleware_records_successful_read_as_bounded_metric(monkeypatch) -> None:
    captured: list[dict] = []
    observed: list[dict] = []

    class _FakeAuditService:
        def emit(self, **kwargs):
            captured.append(kwargs)

    class _FakeMetrics:
        def observe(self, **kwargs):
            observed.append(kwargs)

    monkeypatch.setattr("agent.bootstrap.audit_middleware.get_execution_audit_service", lambda: _FakeAuditService())
    monkeypatch.setattr("agent.bootstrap.audit_middleware.get_http_request_metrics", lambda: _FakeMetrics())
    app = Flask(__name__)
    register_audit_middleware(app)

    @app.get("/api/ping")
    def _ping():
        return {"ok": True}

    response = app.test_client().get("/api/ping")
    assert response.status_code == 200
    assert captured == []
    assert observed == [
        {
            "method": "GET",
            "status_code": 200,
            "duration_ms": observed[0]["duration_ms"],
        }
    ]
    assert observed[0]["duration_ms"] >= 0


def test_audit_middleware_treats_bulk_plan_as_read_only(monkeypatch) -> None:
    captured: list[dict] = []
    observed: list[dict] = []

    class _FakeAuditService:
        def emit(self, **kwargs):
            captured.append(kwargs)

    class _FakeMetrics:
        def observe(self, **kwargs):
            observed.append(kwargs)

    monkeypatch.setattr("agent.bootstrap.audit_middleware.get_execution_audit_service", lambda: _FakeAuditService())
    monkeypatch.setattr("agent.bootstrap.audit_middleware.get_http_request_metrics", lambda: _FakeMetrics())
    app = Flask(__name__)
    register_audit_middleware(app)

    @app.post("/api/source-control/v1/bulk/plan")
    def _bulk_plan():
        return {"dry_run": True}

    response = app.test_client().post("/api/source-control/v1/bulk/plan")
    assert response.status_code == 200
    assert captured == []
    assert len(observed) == 1
    assert observed[0]["method"] == "POST"
    assert observed[0]["status_code"] == 200


def test_audit_middleware_durably_audits_failed_read(monkeypatch) -> None:
    captured: list[dict] = []
    observed: list[dict] = []

    class _FakeAuditService:
        def emit(self, **kwargs):
            captured.append(kwargs)

    class _FakeMetrics:
        def observe(self, **kwargs):
            observed.append(kwargs)

    monkeypatch.setattr("agent.bootstrap.audit_middleware.get_execution_audit_service", lambda: _FakeAuditService())
    monkeypatch.setattr("agent.bootstrap.audit_middleware.get_http_request_metrics", lambda: _FakeMetrics())
    app = Flask(__name__)
    register_audit_middleware(app)

    @app.get("/api/restricted")
    def _restricted():
        return {"error": "forbidden"}, 403

    response = app.test_client().get("/api/restricted")
    assert response.status_code == 403
    assert observed == []
    assert len(captured) == 1
    assert captured[0]["operation_type"] == "http_request_completed"
    assert captured[0]["outcome"] == "error"
    assert captured[0]["details"]["status_code"] == 403
