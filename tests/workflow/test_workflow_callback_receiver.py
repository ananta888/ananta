from __future__ import annotations

from flask import Flask

from agent.routes.integrations_workflows import integrations_workflows_bp
from agent.services.workflow_auth import sign_callback


def _app() -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["WORKFLOW_CALLBACK_SECRET"] = "secret"
    app.config["WORKFLOW_KNOWN_CORRELATIONS"] = {"c1"}
    app.register_blueprint(integrations_workflows_bp)
    return app


def test_callback_accepts_valid_signature() -> None:
    app = _app()
    client = app.test_client()
    sig = sign_callback(secret="secret", correlation_id="c1", provider="mock")
    res = client.post(
        "/api/integrations/workflows/callback",
        json={"provider": "mock", "correlation_id": "c1"},
        headers={"X-Workflow-Signature": sig["signature"], "X-Workflow-Timestamp": sig["timestamp"]},
    )
    assert res.status_code == 200


def test_callback_rejects_invalid_signature() -> None:
    app = _app()
    client = app.test_client()
    res = client.post(
        "/api/integrations/workflows/callback",
        json={"provider": "mock", "correlation_id": "c1"},
        headers={"X-Workflow-Signature": "bad", "X-Workflow-Timestamp": "1"},
    )
    assert res.status_code in {401, 400}


def test_callback_rejects_unknown_correlation_id() -> None:
    app = _app()
    client = app.test_client()
    sig = sign_callback(secret="secret", correlation_id="c-unknown", provider="mock")
    res = client.post(
        "/api/integrations/workflows/callback",
        json={"provider": "mock", "correlation_id": "c-unknown"},
        headers={"X-Workflow-Signature": sig["signature"], "X-Workflow-Timestamp": sig["timestamp"]},
    )
    assert res.status_code == 404
