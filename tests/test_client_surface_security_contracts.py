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
