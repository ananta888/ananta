from __future__ import annotations

from types import SimpleNamespace

from flask import Flask

from agent.services.ops_policy_service import OpsPolicyService


class _ApprovalService:
    def __init__(self, *, row_status: str = "granted", matching: bool = True) -> None:
        self.row = SimpleNamespace(id="approval-1", status=row_status)
        self.matching = matching
        self.resolved_arguments = None
        self.consumed = None

    def get_request(self, request_id: str):
        return self.row if request_id == self.row.id else None

    def resolve_grant_for_call(self, **kwargs):
        self.resolved_arguments = kwargs
        return self.row if self.matching else None

    def get_lifecycle_config(self):
        return {"grant_one_shot": True}

    def consume_request(self, request_id: str):
        self.consumed = request_id
        return self.row


def _app() -> Flask:
    app = Flask(__name__)
    app.config["AGENT_CONFIG"] = {"ops_policy": {"mutating_default": "approval_required"}}
    return app


def test_authorize_accepts_only_digest_bound_matching_grant(monkeypatch) -> None:
    approvals = _ApprovalService()
    monkeypatch.setattr(
        "agent.services.approval_request_service.get_approval_request_service",
        lambda: approvals,
    )
    with _app().app_context():
        decision = OpsPolicyService().authorize(
            "git.stage",
            "stage",
            target_id="repo",
            arguments={"workspace_id": "repo", "paths": ["README.md"]},
            approval_id="approval-1",
        )

    assert decision.allowed is True
    assert decision.reason_code == "approval_granted"
    assert approvals.resolved_arguments == {
        "tool_name": "git.stage",
        "arguments": {"workspace_id": "repo", "paths": ["README.md"]},
        "task_id": None,
        "goal_id": None,
        "target_fingerprint": "repo",
    }


def test_authorize_rejects_grant_for_different_arguments(monkeypatch) -> None:
    approvals = _ApprovalService(matching=False)
    monkeypatch.setattr(
        "agent.services.approval_request_service.get_approval_request_service",
        lambda: approvals,
    )
    with _app().app_context():
        decision = OpsPolicyService().authorize(
            "docker.container_action",
            "restart",
            target_id="container-1",
            arguments={"container_id": "container-2", "action": "restart"},
            approval_id="approval-1",
        )

    assert decision.decision == "policy_denied"
    assert decision.reason_code == "approval_digest_mismatch"


def test_authorize_preserves_pending_state(monkeypatch) -> None:
    approvals = _ApprovalService(row_status="pending")
    monkeypatch.setattr(
        "agent.services.approval_request_service.get_approval_request_service",
        lambda: approvals,
    )
    with _app().app_context():
        decision = OpsPolicyService().authorize(
            "compose.project_action",
            "up",
            target_id="project-1",
            arguments={"project_id": "project-1", "action": "up"},
            approval_id="approval-1",
        )

    assert decision.decision == "approval_required"
    assert decision.reason_code == "approval_pending"


def test_consume_approval_uses_one_shot_lifecycle(monkeypatch) -> None:
    approvals = _ApprovalService()
    monkeypatch.setattr(
        "agent.services.approval_request_service.get_approval_request_service",
        lambda: approvals,
    )
    OpsPolicyService.consume_approval("approval-1")
    assert approvals.consumed == "approval-1"


def test_git_publication_does_not_inherit_workspace_auto_approval(monkeypatch):
    from types import SimpleNamespace
    from agent.services.approval_auto_grant_policy import ApprovalAutoGrantPolicy
    from agent.services.ops_policy_service import OpsPolicyService
    captured = {}

    def create_pending_request(**values):
        captured.update(values)
        return SimpleNamespace(id="synthetic-approval")

    monkeypatch.setattr("agent.services.approval_request_service.get_approval_request_service",
        lambda: SimpleNamespace(create_pending_request=create_pending_request))
    for tool, action in (("git.commit", "commit"), ("git.push", "push")):
        result = OpsPolicyService().create_approval_request(tool_name=tool, action=action,
            target_id="workspace", arguments={"workspace_id": "workspace"})
        assert result == "synthetic-approval"
        assert ApprovalAutoGrantPolicy().reason(policy_by_mode={"balanced": {
            "controlled_workspace_writes": True}}, human_required_tools=[], tool_name=tool,
            scope=captured["scope"], governance_mode="balanced") is None
