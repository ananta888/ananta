from __future__ import annotations

from agent.services.approval_auto_grant_policy import (
    RECOVERY_MATERIALIZE_TOOL,
    ApprovalAutoGrantPolicy,
)


def _recovery_scope() -> dict[str, str]:
    return {
        "approval_class": "task_materialization",
        "source": "model_context_recovery",
        "plan_id": "plan-1",
        "source_task_id": "task-1",
        "recovery_key": "recovery-1",
    }


def test_recovery_materialization_requires_explicit_hub_policy() -> None:
    policy = ApprovalAutoGrantPolicy()

    assert (
        policy.reason(
            policy_by_mode={
                "balanced": {"recovery_plan_materialization": False}
            },
            human_required_tools=[],
            tool_name=RECOVERY_MATERIALIZE_TOOL,
            scope=_recovery_scope(),
            governance_mode="balanced",
        )
        is None
    )
    assert policy.reason(
        policy_by_mode={
            "balanced": {"recovery_plan_materialization": True}
        },
        human_required_tools=[],
        tool_name=RECOVERY_MATERIALIZE_TOOL,
        scope=_recovery_scope(),
        governance_mode="balanced",
    ) == "auto_approved:recovery_plan_materialization"


def test_recovery_auto_approval_is_exactly_scoped_and_human_override_wins() -> None:
    policy = ApprovalAutoGrantPolicy()
    enabled = {"balanced": {"recovery_plan_materialization": True}}

    incomplete = _recovery_scope()
    incomplete.pop("recovery_key")
    assert (
        policy.reason(
            policy_by_mode=enabled,
            human_required_tools=[],
            tool_name=RECOVERY_MATERIALIZE_TOOL,
            scope=incomplete,
            governance_mode="balanced",
        )
        is None
    )
    assert (
        policy.reason(
            policy_by_mode=enabled,
            human_required_tools=[RECOVERY_MATERIALIZE_TOOL],
            tool_name=RECOVERY_MATERIALIZE_TOOL,
            scope=_recovery_scope(),
            governance_mode="balanced",
        )
        is None
    )
