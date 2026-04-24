from __future__ import annotations

import json

from client_surfaces.common.client_api import AnantaApiClient
from client_surfaces.common.profile_auth import build_client_profile


def test_external_client_api_contract_methods_cover_surface_flows() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method, url, _headers, body, _timeout):  # noqa: ANN001
        payload = json.loads(body.decode("utf-8")) if body else None
        calls.append((method, url, payload))
        path = url.split("http://localhost:8080", 1)[-1]
        responses = {
            ("GET", "/health"): (200, {"state": "ready"}),
            ("GET", "/capabilities"): (200, {"capabilities": ["goals", "tasks", "artifacts", "approvals"]}),
            ("GET", "/tasks"): (200, {"items": [{"id": "task-1", "status": "queued", "title": "Analyze file"}]}),
            ("GET", "/artifacts"): (
                200,
                {"items": [{"id": "artifact-1", "type": "report", "title": "Result summary"}]},
            ),
            ("GET", "/approvals"): (200, {"items": [{"id": "approval-1", "scope": "deploy", "state": "pending"}]}),
            ("POST", "/goals"): (200, {"goal_id": "goal-1", "task_id": "task-1"}),
            ("POST", "/tasks/analyze"): (200, {"task_id": "task-analyze-1", "status": "queued"}),
            ("POST", "/tasks/review"): (200, {"task_id": "task-review-1", "status": "queued"}),
            ("POST", "/tasks/patch-plan"): (200, {"task_id": "task-patch-1", "status": "queued"}),
            ("POST", "/projects/new"): (200, {"task_id": "task-project-new-1", "status": "queued"}),
            ("POST", "/projects/evolve"): (200, {"task_id": "task-project-evolve-1", "status": "queued"}),
        }
        status, response_payload = responses[(method, path)]
        return status, json.dumps(response_payload)

    client = AnantaApiClient(
        build_client_profile({"profile_id": "api-contract", "base_url": "http://localhost:8080"}),
        transport=transport,
    )
    context = {"schema": "client_bounded_context_payload_v1", "selection_text": "print('x')"}

    assert client.get_health().data == {"state": "ready"}
    assert "goals" in client.get_capabilities().data["capabilities"]
    assert client.list_tasks().data["items"][0]["id"] == "task-1"
    assert client.list_artifacts().data["items"][0]["type"] == "report"
    assert client.list_approvals().data["items"][0]["state"] == "pending"
    assert client.submit_goal("Demo Goal", context).data["goal_id"] == "goal-1"
    assert client.analyze_context(context).data["task_id"] == "task-analyze-1"
    assert client.review_context(context).data["task_id"] == "task-review-1"
    assert client.patch_plan(context).data["task_id"] == "task-patch-1"
    assert client.create_project_new("Create project", context).data["task_id"] == "task-project-new-1"
    assert client.create_project_evolve("Evolve project", context).data["task_id"] == "task-project-evolve-1"

    called_paths = {(method, url.split("http://localhost:8080", 1)[-1]) for method, url, _ in calls}
    assert ("GET", "/health") in called_paths
    assert ("GET", "/capabilities") in called_paths
    assert ("GET", "/tasks") in called_paths
    assert ("GET", "/artifacts") in called_paths
    assert ("GET", "/approvals") in called_paths
    assert ("POST", "/goals") in called_paths
    assert ("POST", "/tasks/analyze") in called_paths
    assert ("POST", "/tasks/review") in called_paths
    assert ("POST", "/tasks/patch-plan") in called_paths
    assert ("POST", "/projects/new") in called_paths
    assert ("POST", "/projects/evolve") in called_paths


def test_external_client_api_contract_exposes_degraded_response_shapes() -> None:
    def transport(method, url, _headers, _body, _timeout):  # noqa: ANN001
        path = url.split("http://localhost:8080", 1)[-1]
        routes = {
            ("GET", "/capabilities"): (422, '{"error":"capability_missing"}'),
            ("POST", "/goals"): (403, '{"error":"policy_denied"}'),
            ("POST", "/tasks/review"): (200, "not-json"),
            ("POST", "/tasks/analyze"): (401, '{"error":"auth_failed"}'),
        }
        return routes[(method, path)]

    client = AnantaApiClient(
        build_client_profile({"profile_id": "api-contract", "base_url": "http://localhost:8080"}),
        transport=transport,
    )
    context = {"schema": "client_bounded_context_payload_v1", "selection_text": "print('x')"}

    capabilities = client.get_capabilities()
    denied_goal = client.submit_goal("Denied Goal", context)
    malformed_review = client.review_context(context)
    unauthorized_analyze = client.analyze_context(context)

    assert capabilities.state == "capability_missing"
    assert denied_goal.state == "policy_denied"
    assert malformed_review.state == "malformed_response"
    assert unauthorized_analyze.state == "auth_failed"
    assert malformed_review.retriable is True
    assert denied_goal.retriable is False
