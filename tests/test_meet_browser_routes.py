"""Authenticated owner controls; room membership is not Hub browser authority."""

import json
from unittest.mock import Mock

import pytest

from agent.bootstrap.meet_browser import configured_browser_workspaces
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_dialog import request_signature, response_signature
from tests.test_meet_avatar_image_routes import application
from tests.test_meet_browser_completion import payload
from tests.test_meet_browser_hub_tasks import setup


def authenticate(monkeypatch, subject="owner"):
    import agent.auth as auth

    monkeypatch.setattr(
        auth,
        "_validate_user_jwt",
        lambda token: {"sub": subject, "tenant_id": "tenant", "project_id": "project", "role": "user"}
        if token == "synthetic-user"
        else None,
    )
    monkeypatch.setattr(auth, "_user_token_allows_current_request", lambda _: True)


def test_actual_owner_http_navigate_then_present_and_signed_finish_are_fully_headless(app, monkeypatch):
    authenticate(monkeypatch)
    with app.app_context():
        f = setup()
        http, _ = application()
        http.extensions["meet_dialog_service"] = f.service
        client, headers = http.test_client(), {"Authorization": "Bearer synthetic-user"}
        url = f"/api/meet/v1/projects/project/dialogs/{f.task_id}/browser"
        assert client.get(url, headers=headers).json["mode"] == "status"
        result = client.post(
            url, headers=headers, json={"action": "navigate", "expected_revision": 1, "url": "https://example.com/docs"}
        )
        assert result.status_code == 200 and result.json["mode"] == "off"
        assert result.headers["Cache-Control"] == "no-store"
        result = client.post(url, headers=headers, json={"action": "present", "expected_revision": 2})
        assert result.status_code == 200 and result.json["mode"] == "browser"
        job = f.browser_tasks.read(f.scope)["job"]
        raw = json.dumps(payload(f, job)).encode()
        key = http.extensions["meet_media_worker_key"]
        result = client.post(
            "/api/meet/v1/internal/dialog",
            data=raw,
            headers={"Content-Type": "application/json", "X-Ananta-Dialog-Signature": request_signature(key, raw)},
        )
        assert result.status_code == 200 and result.json["schema"] == "ananta.meet-browser-finished.v1"
        assert result.headers["X-Ananta-Dialog-Signature"] == response_signature(key, raw, result.data)
        assert f.tasks.get_by_id(job["task_id"]).status == "failed"


def test_unauthorized_viewer_and_malformed_http_cannot_mutate_browser(app, monkeypatch):
    authenticate(monkeypatch, "viewer")
    with app.app_context():
        f = setup()
        http, _ = application()
        http.extensions["meet_dialog_service"] = f.service
        client = http.test_client()
        url = f"/api/meet/v1/projects/project/dialogs/{f.task_id}/browser"
        request = {"action": "navigate", "expected_revision": 1, "url": "https://example.com"}
        assert client.post(url, json=request).status_code == 401
        headers = {"Authorization": "Bearer synthetic-user"}
        assert client.post(url, headers=headers, json=request).status_code == 403
        authenticate(monkeypatch)
        for raw in (b'{"action":"status","action":"navigate"}', b"null", b"x" * 4097):
            assert client.post(url, headers=headers, data=raw).status_code == 400
        assert client.post(url + "?url=private", headers=headers, json=request).status_code == 400
        assert client.get(url, headers=headers, data=b"not-empty").status_code == 400
        assert f.browser_tasks.read(f.scope)["revision"] == 1


@pytest.mark.parametrize("raw", ["null", '{"not":"rows"}', '[{"revision":1,"revision":2}]', "[" + " " * 131073 + "]"])
def test_operator_config_is_bounded_and_rejects_duplicate_fields(monkeypatch, raw):
    monkeypatch.setenv("ANANTA_MEET_BROWSER_PUBLIC_POLICIES", raw)
    with pytest.raises(ValueError, match="^meet_browser_operator_policy_invalid$"):
        configured_browser_workspaces(Mock(), Mock())


def test_unconfigured_bootstrap_is_deny_all_and_does_not_dispatch(monkeypatch):
    from tests.test_meet_browser_policy import scope

    monkeypatch.delenv("ANANTA_MEET_BROWSER_PUBLIC_POLICIES", raising=False)
    tasks = Mock()
    service = configured_browser_workspaces(Mock(), tasks)
    with pytest.raises(MeetError, match="policy_denied"):
        service.policy.navigation(scope(), "https://example.com")
    assert not tasks.mock_calls
