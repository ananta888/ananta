from __future__ import annotations

from agent.routes.codecompass_reload import codecompass_reload_bp


def test_reload_context_requires_authentication(app) -> None:
    app.register_blueprint(codecompass_reload_bp)
    response = app.test_client().post(
        "/api/codecompass/reload-context",
        json={"task_id": "task-example", "request": {}},
    )

    assert response.status_code == 401
