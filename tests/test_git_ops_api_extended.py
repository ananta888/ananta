from __future__ import annotations

import pytest

from agent.services.ops_models import OpsActionResult


@pytest.mark.parametrize(
    "path",
    [
        "/api/ops/git/workspaces",
        "/api/ops/git/status?workspace_id=repo",
        "/api/ops/git/changes?workspace_id=repo",
        "/api/ops/git/diff?workspace_id=repo&scope=combined",
        "/api/ops/git/history?workspace_id=repo&limit=5&offset=0",
        "/api/ops/git/branches?workspace_id=repo",
        "/api/ops/git/remotes?workspace_id=repo",
        "/api/ops/git/activity?workspace_id=repo&limit=5",
    ],
)
def test_git_read_routes_return_structured_authenticated_responses(client, auth_header, path):
    response = client.get(path, headers=auth_header)
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert isinstance(payload["data"], dict)


class RecordingGitService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def invoke(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return OpsActionResult(
                True,
                name,
                target_id=str(args[0] if args else ""),
                approval_id=str(kwargs.get("approval_id") or "") or None,
            )

        return invoke


@pytest.mark.parametrize(
    ("route", "body", "method"),
    [
        ("/api/ops/git/stage", {"workspace_id": "w1", "paths": ["a.txt"], "approval_id": "grant-1"}, "stage"),
        ("/api/ops/git/unstage", {"workspace_id": "w1", "paths": ["a.txt"], "approval_id": "grant-1"}, "unstage"),
        ("/api/ops/git/discard", {"workspace_id": "w1", "paths": ["a.txt"], "approval_id": "grant-1"}, "discard"),
        (
            "/api/ops/git/commit",
            {"workspace_id": "w1", "message": "feat(test): verify route", "approval_id": "grant-1"},
            "commit",
        ),
        ("/api/ops/git/fetch", {"workspace_id": "w1", "remote": "origin", "approval_id": "grant-1"}, "fetch"),
        (
            "/api/ops/git/pull",
            {"workspace_id": "w1", "remote": "origin", "branch": "main", "approval_id": "grant-1"},
            "pull",
        ),
        (
            "/api/ops/git/push",
            {"workspace_id": "w1", "remote": "origin", "branch": "main", "approval_id": "grant-1"},
            "push",
        ),
    ],
)
def test_git_mutation_routes_are_admin_scoped_and_forward_approval(
    client,
    admin_auth_header,
    monkeypatch,
    route,
    body,
    method,
):
    service = RecordingGitService()
    monkeypatch.setattr("agent.routes.ops.get_git_ops_service", lambda: service)

    response = client.post(route, headers=admin_auth_header, json=body)
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert service.calls[0][0] == method
    assert service.calls[0][2]["approval_id"] == "grant-1"
