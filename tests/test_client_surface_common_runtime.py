from __future__ import annotations

from client_surfaces.common.client_api import AnantaApiClient
from client_surfaces.common.context_packaging import package_editor_context
from client_surfaces.common.degraded_state import is_retriable_state, map_status_to_degraded_state
from client_surfaces.common.profile_auth import (
    build_client_profile,
    contains_secret_key,
    redact_sensitive_text,
    sanitize_profile_for_persistence,
)


def test_build_client_profile_and_persistence_redacts_secret_fields() -> None:
    profile = build_client_profile(
        {
            "profile_id": "ops-local",
            "base_url": "http://localhost:8080/",
            "auth_mode": "session_token",
            "auth_token": "secret-token",
            "environment": "Local",
            "timeout_seconds": 12,
        }
    )
    persisted = sanitize_profile_for_persistence(profile)
    assert profile.base_url == "http://localhost:8080"
    assert profile.auth_token == "secret-token"
    assert "auth_token" not in persisted
    assert persisted["profile_id"] == "ops-local"


def test_profile_helpers_detect_and_redact_secrets() -> None:
    assert contains_secret_key("api_token") is True
    assert contains_secret_key("project_name") is False
    assert redact_sensitive_text("token=abc123 password=foo") == "token=*** password=***"


def test_package_editor_context_is_bounded_and_tracks_provenance() -> None:
    payload = package_editor_context(
        file_path="/workspace/src/main.py",
        project_root="/workspace",
        selection_text="A" * 2500,
        extra_paths=["/workspace/src/a.py", "/outside/secrets.txt"],
        max_selection_chars=1000,
        max_paths=10,
    )
    assert payload["schema"] == "client_bounded_context_payload_v1"
    assert payload["selection_clipped"] is True
    assert payload["provenance"]["extra_paths_count"] == 1
    assert payload["extra_paths"] == ["/workspace/src/a.py"]
    assert payload["rejected_paths"] == ["/outside/secrets.txt"]


def test_package_editor_context_warns_for_secret_like_selection() -> None:
    payload = package_editor_context(
        file_path="/workspace/.env",
        project_root="/workspace",
        selection_text="API_KEY=123",
    )
    assert "selection_may_contain_secret" in payload["warnings"]


def test_degraded_state_mapping_and_retriable_logic() -> None:
    assert map_status_to_degraded_state(200) == "healthy"
    assert map_status_to_degraded_state(401) == "auth_failed"
    assert map_status_to_degraded_state(403) == "policy_denied"
    assert map_status_to_degraded_state(422) == "capability_missing"
    assert map_status_to_degraded_state(None) == "backend_unreachable"
    assert map_status_to_degraded_state(200, parse_error=True) == "malformed_response"
    assert is_retriable_state("backend_unreachable") is True
    assert is_retriable_state("policy_denied") is False


def test_ananta_api_client_uses_transport_and_returns_typed_states() -> None:
    def transport(method, url, _headers, _body, _timeout):  # noqa: ANN001
        if method == "GET" and url.endswith("/health"):
            return 200, '{"state":"ready"}'
        if method == "GET" and url.endswith("/capabilities"):
            return 422, '{"error":"missing_capability"}'
        return 404, '{"error":"not_found"}'

    client = AnantaApiClient(
        build_client_profile({"profile_id": "dev", "base_url": "http://localhost:8080"}),
        transport=transport,
    )
    health = client.get_health()
    capabilities = client.get_capabilities()
    assert health.ok is True
    assert health.state == "healthy"
    assert capabilities.ok is False
    assert capabilities.state == "capability_missing"


def test_ananta_api_client_handles_backend_unreachable_and_malformed_payload() -> None:
    def failing_transport(_method, _url, _headers, _body, _timeout):  # noqa: ANN001
        raise ConnectionError("dial failed")

    def malformed_transport(_method, _url, _headers, _body, _timeout):  # noqa: ANN001
        return 200, "not-json"

    client_unreachable = AnantaApiClient(
        build_client_profile({"profile_id": "dev", "base_url": "http://localhost:8080"}),
        transport=failing_transport,
    )
    client_malformed = AnantaApiClient(
        build_client_profile({"profile_id": "dev", "base_url": "http://localhost:8080"}),
        transport=malformed_transport,
    )

    unreachable = client_unreachable.list_tasks()
    malformed = client_malformed.list_tasks()
    assert unreachable.state == "backend_unreachable"
    assert unreachable.retriable is True
    assert malformed.state == "malformed_response"
    assert malformed.retriable is True
