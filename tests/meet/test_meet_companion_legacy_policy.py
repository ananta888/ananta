"""Hub retirement gate keeps old routes reviewable without Worker-owned retries."""

import pytest
from flask import Flask

from agent.routes.meet import meet_bp
from agent.services.meet_companion_legacy_policy import require_legacy_companion
from agent.services.meet_contract import MeetError


@pytest.mark.parametrize("path", ["/internal/companion", "/internal/assist/tool", "/internal/assist/retrieve"])
def test_assigned_mode_retires_actual_legacy_http_before_service_execution(path, monkeypatch):
    monkeypatch.delenv("ANANTA_MEET_COMPANION_LEGACY_ENABLED", raising=False)
    app = Flask(__name__)
    app.config.update(ROLE="hub", TESTING=True)
    app.register_blueprint(meet_bp)
    app.extensions["meet_companion_task_grants"] = object()
    response = app.test_client().post("/api/meet/v1" + path, json={})
    assert response.status_code == 410
    assert "meet_companion_legacy_retired_use_hub_tasks" in response.get_data(as_text=True)


def test_legacy_only_installation_keeps_compatibility_unless_operator_disables(monkeypatch):
    monkeypatch.delenv("ANANTA_MEET_COMPANION_LEGACY_ENABLED", raising=False)
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    require_legacy_companion(app, "/api/meet/v1/internal/companion")
    app.config["ANANTA_MEET_COMPANION_LEGACY_ENABLED"] = False
    with pytest.raises(MeetError, match="retired"):
        require_legacy_companion(app, "/api/meet/v1/internal/companion")


def test_explicit_hub_compatibility_policy_does_not_retire_assigned_endpoints(monkeypatch):
    monkeypatch.delenv("ANANTA_MEET_COMPANION_LEGACY_ENABLED", raising=False)
    app = Flask(__name__)
    app.config.update(ROLE="hub", ANANTA_MEET_COMPANION_LEGACY_ENABLED=True)
    app.extensions["meet_companion_task_grants"] = object()
    require_legacy_companion(app, "/api/meet/v1/internal/companion")
    app.config["ANANTA_MEET_COMPANION_LEGACY_ENABLED"] = False
    require_legacy_companion(app, "/api/meet/v1/internal/companion/task-grant")


def test_invalid_operator_policy_is_bounded_denial(monkeypatch):
    monkeypatch.delenv("ANANTA_MEET_COMPANION_LEGACY_ENABLED", raising=False)
    app = Flask(__name__)
    app.config.update(ROLE="hub", ANANTA_MEET_COMPANION_LEGACY_ENABLED="implicit")
    with pytest.raises(MeetError, match="policy_invalid"):
        require_legacy_companion(app, "/api/meet/v1/internal/companion")
