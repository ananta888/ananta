from __future__ import annotations

from flask import Flask

from agent.routes.codecompass_reload import codecompass_reload_bp


def test_reload_context_requires_authentication() -> None:
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        AGENT_TOKEN="test-agent-token-with-sufficient-length-1234567890",
    )
    app.register_blueprint(codecompass_reload_bp)
    response = app.test_client().post(
        "/api/codecompass/reload-context",
        json={"task_id": "task-example", "request": {}},
    )

    assert response.status_code == 401
