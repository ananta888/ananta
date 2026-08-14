from __future__ import annotations

from typing import Any

import pytest
from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes import visual_process as visual_process_routes
from agent.routes.visual_process import vp_bp


class _TerminalStatusBackend:
    def __init__(self, status: str) -> None:
        self._status = status

    def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        return {
            "schema": "ananta.workflow_backend_status.v1",
            "workflow_id": workflow_id,
            "run_id": "terminal-run",
            "revision": 4,
            "status": self._status,
            "updated_at": "2026-08-14T00:00:00Z",
            "steps": [],
        }


def _headers() -> dict[str, str]:
    token = generate_token(
        {"sub": "terminal-owner", "tenant_id": "terminal-tenant", "role": "user"},
        settings.secret_key,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled", "succeeded"])
def test_workflow_status_route_returns_terminal_domain_status_as_success(
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(vp_bp)
    backend = _TerminalStatusBackend(terminal_status)
    monkeypatch.setattr(
        visual_process_routes,
        "require_workflow_owner",
        lambda _workflow_id: (object(), None),
    )
    monkeypatch.setattr(
        visual_process_routes,
        "configured_workflow_backend",
        lambda _principal: (backend, None),
    )

    response = app.test_client().get(
        "/api/visual-process/workflow/terminal-workflow/status",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == terminal_status
