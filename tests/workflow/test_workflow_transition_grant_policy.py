"""Deriving an authorization grant from the execution plan it belongs to.

The property that matters: a grant can never permit more than the plan
declares, and a step that declares nothing gets no grant at all rather than an
empty one that would make an unauthorized step look authorized.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.services.workflow_runtime.execution_plan import (
    ExecutionBudget,
    ExecutionNode,
    ExecutionPlan,
)
from agent.services.workflow_transition_grant_policy import (
    MAX_GRANT_TTL_SECONDS,
    ExecutionPlanGrantPolicy,
    WorkflowTransitionGrantPlan,
    WorkflowTransitionGrantPolicyError,
    grant_plan_to_mapping,
)


def _plan(*nodes: ExecutionNode) -> ExecutionPlan:
    return ExecutionPlan(
        tenant_id="tenant-a",
        plan_id="plan-a",
        workflow_id="workflow-a",
        policy_version="policy-v1",
        nodes=tuple(nodes),
    )


def _node(**overrides: Any) -> ExecutionNode:
    values: dict[str, Any] = {
        "node_id": "step-a",
        "allowed_tools": ("shell", "git"),
        "input_artifacts": ("in.md",),
        "output_artifacts": ("out.md",),
        "budget": ExecutionBudget(max_attempts=3, timeout_seconds=60.0, max_tokens=1_000),
    }
    values.update(overrides)
    return ExecutionNode(**values)


def test_a_grant_carries_exactly_what_the_plan_node_declares() -> None:
    derived = ExecutionPlanGrantPolicy().derive(_plan(_node()), step_id="step-a")

    assert derived.allowed_tools == ("shell", "git")
    assert derived.allowed_artifacts == ("in.md", "out.md")
    assert derived.budgets == {
        "max_attempts": 3,
        "max_tokens": 1_000,
        "timeout_seconds": 60.0,
    }


def test_a_step_the_plan_does_not_contain_yields_no_grant() -> None:
    with pytest.raises(WorkflowTransitionGrantPolicyError, match="step_not_planned"):
        ExecutionPlanGrantPolicy().derive(_plan(_node()), step_id="step-unknown")


def test_a_step_that_declares_no_scope_yields_no_grant() -> None:
    """An empty grant would make an unauthorized step look authorized."""

    node = _node(allowed_tools=(), input_artifacts=(), output_artifacts=())

    with pytest.raises(WorkflowTransitionGrantPolicyError, match="scope_empty"):
        ExecutionPlanGrantPolicy().derive(_plan(node), step_id="step-a")


def test_artifacts_are_deduplicated_across_inputs_and_outputs() -> None:
    node = _node(input_artifacts=("shared.md", "in.md"), output_artifacts=("shared.md",))

    derived = ExecutionPlanGrantPolicy().derive(_plan(node), step_id="step-a")

    assert derived.allowed_artifacts == ("shared.md", "in.md")


def test_the_ttl_is_bounded_by_the_work_the_grant_authorizes() -> None:
    """Timeout times attempts, with headroom — not an open-ended lifetime."""

    node = _node(budget=ExecutionBudget(max_attempts=3, timeout_seconds=60.0))

    derived = ExecutionPlanGrantPolicy().derive(_plan(node), step_id="step-a")

    assert derived.ttl_seconds == 720.0


def test_the_ttl_never_exceeds_the_configured_maximum() -> None:
    node = _node(budget=ExecutionBudget(max_attempts=1_000, timeout_seconds=100_000.0))

    derived = ExecutionPlanGrantPolicy(maximum_ttl_seconds=3_600.0).derive(_plan(node), step_id="step-a")

    assert derived.ttl_seconds == 3_600.0


def test_a_node_without_a_usable_timeout_falls_back_to_the_maximum() -> None:
    node = _node(budget=ExecutionBudget(max_attempts=1, timeout_seconds=0.0))

    derived = ExecutionPlanGrantPolicy(maximum_ttl_seconds=900.0).derive(_plan(node), step_id="step-a")

    assert derived.ttl_seconds == 900.0


@pytest.mark.parametrize("maximum", (0.0, -1.0, MAX_GRANT_TTL_SECONDS + 1, True))
def test_an_out_of_range_maximum_ttl_is_rejected(maximum: Any) -> None:
    with pytest.raises(WorkflowTransitionGrantPolicyError, match="maximum_ttl_invalid"):
        ExecutionPlanGrantPolicy(maximum_ttl_seconds=maximum)


def test_a_malformed_plan_is_rejected_rather_than_read_as_empty() -> None:
    class _NotAPlan:
        nodes = "not a sequence of nodes"

    with pytest.raises(WorkflowTransitionGrantPolicyError, match="plan_invalid"):
        ExecutionPlanGrantPolicy().derive(_NotAPlan(), step_id="step-a")


@pytest.mark.parametrize("step_id", ("", None, 7))
def test_an_invalid_step_identity_is_rejected(step_id: Any) -> None:
    with pytest.raises(WorkflowTransitionGrantPolicyError, match="step_invalid"):
        ExecutionPlanGrantPolicy().derive(_plan(_node()), step_id=step_id)


def test_a_derived_grant_renders_for_the_grant_effect_builder() -> None:
    derived = ExecutionPlanGrantPolicy().derive(_plan(_node()), step_id="step-a")

    rendered = grant_plan_to_mapping(derived)

    assert rendered["allowed_tools"] == ["shell", "git"]
    assert rendered["allowed_artifacts"] == ["in.md", "out.md"]
    assert rendered["ttl_seconds"] == derived.ttl_seconds


def test_a_grant_plan_rejects_an_out_of_range_ttl_directly() -> None:
    with pytest.raises(WorkflowTransitionGrantPolicyError, match="ttl_invalid"):
        WorkflowTransitionGrantPlan(
            step_id="step-a",
            allowed_tools=("shell",),
            allowed_artifacts=(),
            budgets={},
            ttl_seconds=MAX_GRANT_TTL_SECONDS + 1.0,
        )


def test_two_derivations_of_the_same_plan_agree() -> None:
    """Derivation is a pure function of the plan, so it cannot drift."""

    policy = ExecutionPlanGrantPolicy()
    plan = _plan(_node())

    assert policy.derive(plan, step_id="step-a") == policy.derive(plan, step_id="step-a")
