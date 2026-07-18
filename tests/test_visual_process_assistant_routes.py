from __future__ import annotations

import pytest

from agent.config import settings
from agent.services.visual_process_assistant_service import VisualProcessAssistantError


def test_assistant_context_route_requires_authentication(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "visual_process_assistant_chat_enabled", True)

    response = client.post("/api/visual-process/assistant/v1/contexts", json={})

    assert response.status_code == 401


def test_assistant_chat_flag_is_fail_closed(client, admin_auth_header, monkeypatch) -> None:
    monkeypatch.setattr(settings, "visual_process_assistant_chat_enabled", False)

    response = client.get(
        "/api/visual-process/assistant/v1/contexts/missing",
        headers=admin_auth_header,
    )

    assert response.status_code == 404
    assert response.get_json()["error_code"] == "assistant_feature_disabled"


@pytest.mark.parametrize(
    ("status_code", "reason_code"),
    [
        (403, "assistant_source_tenant_forbidden"),
        (404, "assistant_context_not_found"),
        (409, "assistant_idempotency_conflict"),
    ],
)
def test_assistant_api_preserves_stable_security_and_conflict_statuses(
    client,
    admin_auth_header,
    monkeypatch,
    status_code: int,
    reason_code: str,
) -> None:
    class _Service:
        @staticmethod
        def get_context(**_kwargs):
            raise VisualProcessAssistantError(reason_code, status_code=status_code)

    monkeypatch.setattr(settings, "visual_process_assistant_chat_enabled", True)
    monkeypatch.setattr(
        "agent.routes.visual_process_assistant.visual_process_assistant_service",
        _Service(),
    )

    response = client.get(
        "/api/visual-process/assistant/v1/contexts/context-1",
        headers=admin_auth_header,
    )

    assert response.status_code == status_code
    assert response.get_json()["error_code"] == reason_code
    assert response.headers["Cache-Control"] == "no-store"


def test_assistant_rate_limit_exposes_retry_after(client, admin_auth_header, monkeypatch) -> None:
    class _Service:
        @staticmethod
        def submit_question(**_kwargs):
            raise VisualProcessAssistantError(
                "assistant_principal_rate_limit",
                status_code=429,
                retry_after=17,
            )

    monkeypatch.setattr(settings, "visual_process_assistant_chat_enabled", True)
    monkeypatch.setattr(
        "agent.routes.visual_process_assistant.visual_process_assistant_service",
        _Service(),
    )

    response = client.post(
        "/api/visual-process/assistant/v1/conversations/conversation-1/questions",
        headers={**admin_auth_header, "Idempotency-Key": "idem-1"},
        json={"question": "Hilfe", "client_request_id": "client-1"},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "17"
    assert response.get_json()["error_code"] == "assistant_principal_rate_limit"


def test_assistant_retry_endpoint_is_additive(client, admin_auth_header, monkeypatch) -> None:
    class _Service:
        @staticmethod
        def retry_request(**kwargs):
            return {
                "request_id": "new-request",
                "status": "queued_retrieval",
                "client_request_id": kwargs["client_request_id"],
            }

    monkeypatch.setattr(settings, "visual_process_assistant_chat_enabled", True)
    monkeypatch.setattr(
        "agent.routes.visual_process_assistant.visual_process_assistant_service",
        _Service(),
    )

    response = client.post(
        "/api/visual-process/assistant/v1/requests/old-request/retry",
        headers={**admin_auth_header, "Idempotency-Key": "retry-idem"},
        json={"client_request_id": "retry-client"},
    )

    assert response.status_code == 202
    assert response.get_json() == {
        "request_id": "new-request",
        "status": "queued_retrieval",
        "client_request_id": "retry-client",
    }


def test_assistant_patch_refresh_endpoint_delegates_a_new_hub_request(
    client,
    admin_auth_header,
    monkeypatch,
) -> None:
    captured = {}

    class _Service:
        @staticmethod
        def refresh_patch_request(**kwargs):
            captured.update(kwargs)
            return {
                "request_id": "refreshed-request",
                "refresh_of_request_id": kwargs["request_id"],
                "status": "queued_retrieval",
            }

    monkeypatch.setattr(settings, "visual_process_assistant_chat_enabled", True)
    monkeypatch.setattr(settings, "visual_process_ai_patches_enabled", True)
    monkeypatch.setattr(
        "agent.routes.visual_process_assistant.visual_process_assistant_service",
        _Service(),
    )
    draft = {"id": "graph-1", "name": "Current draft", "steps": [], "edges": []}

    response = client.post(
        "/api/visual-process/assistant/v1/requests/old-request/patch-refresh",
        headers={**admin_auth_header, "Idempotency-Key": "refresh-idem"},
        json={"draft_graph": draft, "client_request_id": "refresh-client"},
    )

    assert response.status_code == 202
    assert response.get_json()["refresh_of_request_id"] == "old-request"
    assert captured["request_id"] == "old-request"
    assert captured["payload"]["draft_graph"] == draft
    assert captured["client_request_id"] == "refresh-client"
    assert captured["idempotency_key"] == "refresh-idem"
    assert captured["patch_enabled"] is True
