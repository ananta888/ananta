from __future__ import annotations

from client_surfaces.common.client_api import AnantaApiClient
from client_surfaces.common.context_packaging import package_editor_context
from client_surfaces.common.profile_auth import (
    build_client_profile,
    redact_sensitive_text,
    sanitize_profile_for_persistence,
)


def test_client_originated_actions_do_not_bypass_policy_or_auth_failures() -> None:
    def transport(method, url, _headers, _body, _timeout):  # noqa: ANN001
        path = url.split("http://localhost:8080", 1)[-1]
        routes = {
            ("POST", "/goals"): (403, '{"error":"policy_denied"}'),
            ("POST", "/tasks/analyze"): (401, '{"error":"unauthorized"}'),
            ("GET", "/approvals"): (403, '{"error":"policy_denied"}'),
        }
        return routes[(method, path)]

    client = AnantaApiClient(
        build_client_profile({"profile_id": "security", "base_url": "http://localhost:8080"}),
        transport=transport,
    )
    context = {"schema": "client_bounded_context_payload_v1", "selection_text": "review this"}

    denied_goal = client.submit_goal("Needs approval", context)
    unauthorized_analyze = client.analyze_context(context)
    denied_approvals = client.list_approvals()

    assert denied_goal.ok is False and denied_goal.state == "policy_denied"
    assert unauthorized_analyze.ok is False and unauthorized_analyze.state == "auth_failed"
    assert denied_approvals.ok is False and denied_approvals.state == "policy_denied"


def test_client_secret_fields_are_redacted_for_persistence_and_error_views() -> None:
    profile = build_client_profile(
        {
            "profile_id": "ops",
            "base_url": "http://localhost:8080",
            "auth_token": "super-secret-token",
            "auth_mode": "session_token",
            "environment": "prod",
        }
    )
    sanitized = sanitize_profile_for_persistence(profile)
    redacted = redact_sensitive_text("token=abc123 password=abc api_key=abc")

    assert "auth_token" not in sanitized
    assert redacted == "token=*** password=*** api_key=***"


def test_client_security_shapes_cover_malformed_and_denied_responses() -> None:
    def transport(method, url, _headers, _body, _timeout):  # noqa: ANN001
        path = url.split("http://localhost:8080", 1)[-1]
        routes = {
            ("POST", "/tasks/review"): (200, "not-json"),
            ("POST", "/tasks/patch-plan"): (403, '{"error":"policy_denied"}'),
            ("POST", "/projects/evolve"): (401, '{"error":"unauthorized"}'),
            ("POST", "/projects/new"): (422, '{"error":"missing_capability"}'),
        }
        return routes[(method, path)]

    client = AnantaApiClient(
        build_client_profile({"profile_id": "security", "base_url": "http://localhost:8080"}),
        transport=transport,
    )
    context = {"schema": "client_bounded_context_payload_v1", "selection_text": "x"}

    malformed_review = client.review_context(context)
    denied_patch = client.patch_plan(context)
    unauthorized_evolve = client.create_project_evolve("evolve", context)
    missing_capability_new = client.create_project_new("new", context)

    assert malformed_review.state == "malformed_response"
    assert denied_patch.state == "policy_denied"
    assert unauthorized_evolve.state == "auth_failed"
    assert missing_capability_new.state == "capability_missing"


def test_context_packaging_enforces_size_and_provenance_constraints() -> None:
    payload = package_editor_context(
        file_path="/workspace/src/main.py",
        project_root="/workspace",
        selection_text="X" * 5000,
        extra_paths=[
            "/workspace/src/a.py",
            "/workspace/src/b.py",
            "/workspace/src/c.py",
            "/outside/leak.txt",
        ],
        max_selection_chars=1200,
        max_paths=4,
    )

    assert payload["selection_clipped"] is True
    assert len(payload["selection_text"]) == 1200
    assert payload["bounded"] is True
    assert payload["implicit_unrelated_paths_included"] is False
    assert payload["extra_paths"] == ["/workspace/src/a.py", "/workspace/src/b.py", "/workspace/src/c.py"]
    assert payload["rejected_paths"] == ["/outside/leak.txt"]
    assert payload["provenance"]["extra_paths_count"] == 3


def test_client_auth_and_degraded_states_cover_missing_invalid_and_unreachable_paths() -> None:
    def transport(method, url, headers, _body, _timeout):  # noqa: ANN001
        path = url.split("http://localhost:8080", 1)[-1]
        if (method, path) == ("GET", "/health"):
            auth = headers.get("Authorization")
            if not auth:
                return 401, '{"error":"missing_token"}'
            if auth == "Bearer invalid-token":
                return 401, '{"error":"invalid_token","detail":"token=invalid-token"}'
            return 200, '{"state":"ready"}'
        if (method, path) == ("GET", "/capabilities"):
            return 422, '{"error":"missing_capability"}'
        raise AssertionError(f"unexpected route: {(method, path)}")

    missing_token_client = AnantaApiClient(
        build_client_profile({"profile_id": "security-missing", "base_url": "http://localhost:8080", "auth_token": ""}),
        transport=transport,
    )
    invalid_token_client = AnantaApiClient(
        build_client_profile(
            {"profile_id": "security-invalid", "base_url": "http://localhost:8080", "auth_token": "invalid-token"}
        ),
        transport=transport,
    )

    def failing_transport(_method, _url, _headers, _body, _timeout):  # noqa: ANN001
        raise ConnectionError("dial failed token=invalid-token")

    unreachable_client = AnantaApiClient(
        build_client_profile({"profile_id": "security-unreachable", "base_url": "http://localhost:8080"}),
        transport=failing_transport,
    )

    missing = missing_token_client.get_health()
    invalid = invalid_token_client.get_health()
    capability_missing = invalid_token_client.get_capabilities()
    unreachable = unreachable_client.list_tasks()

    assert missing.state == "auth_failed"
    assert invalid.state == "auth_failed"
    assert capability_missing.state == "capability_missing"
    assert unreachable.state == "backend_unreachable"

    assert invalid.error == "request_failed:auth_failed"
    assert "invalid-token" not in invalid.error
    assert "invalid-token" not in redact_sensitive_text(str(invalid.data))
    assert "invalid-token" not in unreachable.error
    assert unreachable.retriable is True


def test_context_packaging_captures_selection_file_project_and_bounded_paths() -> None:
    payload = package_editor_context(
        file_path="/workspace/src/main.py",
        project_root="/workspace",
        selection_text="token=abc123\nprint('hi')",
        extra_paths=[
            "/workspace/src/a.py",
            "/workspace/src/b.py",
            "/workspace/src/c.py",
            "/outside/secret.txt",
        ],
        max_selection_chars=32,
        max_paths=3,
    )

    assert payload["file_path"] == "/workspace/src/main.py"
    assert payload["project_root"] == "/workspace"
    assert payload["selection_text"] == "token=abc123\nprint('hi')"
    assert payload["selection_clipped"] is False
    assert payload["extra_paths"] == ["/workspace/src/a.py", "/workspace/src/b.py", "/workspace/src/c.py"]
    assert payload["rejected_paths"] == []
    assert payload["provenance"] == {
        "has_selection": True,
        "has_file_path": True,
        "has_project_root": True,
        "extra_paths_count": 3,
    }
    assert "selection_may_contain_secret" in payload["warnings"]


def test_approval_requests_cover_unauthorized_and_malformed_action_paths() -> None:
    def transport(method, url, _headers, body, _timeout):  # noqa: ANN001
        path = url.split("http://localhost:8080", 1)[-1]
        if (method, path) == ("GET", "/tasks/task-1"):
            return 200, '{"id":"task-1","proposal_state":"pending"}'
        if (method, path) == ("POST", "/tasks/task-1/review"):
            payload = (body or b"").decode("utf-8")
            if '"action": "approve"' in payload:
                return 401, '{"error":"unauthorized"}'
            return 422, '{"error":"malformed_action"}'
        raise AssertionError(f"unexpected route: {(method, path)}")

    client = AnantaApiClient(
        build_client_profile({"profile_id": "approval-security", "base_url": "http://localhost:8080"}),
        transport=transport,
    )
    unauthorized = client.review_task_proposal("task-1", action="approve")
    malformed = client.review_task_proposal("task-1", action="invalid")

    assert unauthorized.state == "auth_failed"
    assert malformed.state == "capability_missing"
