"""UTCR-010: Tests for UnifiedToolExecutionService."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.services.unified_tool_execution_service import UnifiedToolExecutionService


@pytest.fixture()
def svc() -> UnifiedToolExecutionService:
    return UnifiedToolExecutionService()


def _make_decision(decision: str, reason: str = "test", risk_class: str = "read"):
    from agent.services.ananta_tool_policy_service import ToolPolicyDecision

    return ToolPolicyDecision(
        decision=decision,
        reason=reason,
        rule_id="test_rule",
        risk_class=risk_class,
        tool_name="test.tool",
    )


def test_policy_blocked_returns_build_tool_result(svc):
    blocked_decision = _make_decision("policy_blocked", reason="tool_not_in_allowed_scope")
    with patch(
        "agent.services.ananta_tool_policy_service.AnantaToolPolicyService.evaluate",
        return_value=blocked_decision,
    ):
        result = svc.execute(tool_name="shell.run_unrestricted", arguments={})

    assert result["status"] == "policy_blocked"
    assert "policy_decision" in result
    assert result["policy_decision"]["decision"] == "policy_blocked"
    assert "tool_name" in result


def test_approval_required_returns_correct_status(svc):
    approval_decision = _make_decision("approval_required", reason="write_tool_requires_hub_approval")
    with patch(
        "agent.services.ananta_tool_policy_service.AnantaToolPolicyService.evaluate",
        return_value=approval_decision,
    ):
        result = svc.execute(tool_name="todo.create_or_update", arguments={"path": "x", "content": "y"})

    assert result["status"] == "approval_required"
    assert result["policy_decision"]["decision"] == "approval_required"


def test_allowed_calls_execute_ananta_tool_and_attaches_policy(svc):
    allow_decision = _make_decision("allow")
    fake_result = {
        "schema": "ananta_tool_result.v1",
        "tool_call_id": "",
        "tool_name": "repo.list_files",
        "status": "ok",
        "risk_class": "read",
        "evidence": [],
        "warnings": [],
    }

    with patch(
        "agent.services.ananta_tool_policy_service.AnantaToolPolicyService.evaluate",
        return_value=allow_decision,
    ), patch(
        "agent.services.tools.execute_ananta_tool",
        return_value=dict(fake_result),
    ) as mock_execute:
        result = svc.execute(
            tool_name="repo.list_files",
            arguments={"path_glob": "*.py"},
            workspace_dir="/tmp",
        )

    assert mock_execute.called
    assert result["status"] == "ok"
    assert "policy_decision" in result
    assert result["policy_decision"]["decision"] == "allow"
