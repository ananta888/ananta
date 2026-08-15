"""Derive an authorization grant from the execution plan it belongs to.

A grant says what a step may do.  The execution plan already says exactly
that — per node it declares allowed tools, the artifacts it reads and writes,
and its budget — and the plan is Hub-owned and bound into the binding through
``plan_hash``, which the grant itself signs over.

Deriving the grant from the plan rather than from separate configuration is
therefore not a convenience: a separately configured policy could drift from
the plan, and a grant that permits more than the plan declares is precisely
the gap the grant mechanism exists to close.

A step that declares no permissions yields no grant.  Silently issuing an
empty grant would make an unauthorized step look authorized.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, final

MAX_GRANT_TTL_SECONDS = 31_536_000.0
_DEFAULT_TTL_HEADROOM = 4.0
_MIN_TTL_SECONDS = 1.0


class WorkflowTransitionGrantPolicyError(ValueError):
    """Stable fail-closed grant derivation error."""


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionGrantPlan:
    """Exactly the grant inputs a plan node justifies."""

    step_id: str
    allowed_tools: tuple[str, ...]
    allowed_artifacts: tuple[str, ...]
    budgets: dict[str, int | float]
    ttl_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.step_id, str) or not self.step_id:
            raise WorkflowTransitionGrantPolicyError("workflow_transition_grant_policy_step_invalid")
        if not self.allowed_tools and not self.allowed_artifacts:
            raise WorkflowTransitionGrantPolicyError("workflow_transition_grant_policy_scope_empty")
        if (
            isinstance(self.ttl_seconds, bool)
            or not isinstance(self.ttl_seconds, float)
            or not math.isfinite(self.ttl_seconds)
            or not _MIN_TTL_SECONDS <= self.ttl_seconds <= MAX_GRANT_TTL_SECONDS
        ):
            raise WorkflowTransitionGrantPolicyError("workflow_transition_grant_policy_ttl_invalid")


@final
class ExecutionPlanGrantPolicy:
    """Map one execution plan node onto the grant it justifies."""

    __slots__ = ("_maximum_ttl_seconds",)

    def __init__(self, *, maximum_ttl_seconds: float = MAX_GRANT_TTL_SECONDS) -> None:
        if (
            isinstance(maximum_ttl_seconds, bool)
            or not isinstance(maximum_ttl_seconds, (int, float))
            or not math.isfinite(float(maximum_ttl_seconds))
            or not _MIN_TTL_SECONDS <= float(maximum_ttl_seconds) <= MAX_GRANT_TTL_SECONDS
        ):
            raise WorkflowTransitionGrantPolicyError("workflow_transition_grant_policy_maximum_ttl_invalid")
        self._maximum_ttl_seconds = float(maximum_ttl_seconds)

    def derive(self, plan: Any, *, step_id: str) -> WorkflowTransitionGrantPlan:
        """Derive the grant for one step, or fail closed if the plan has none."""

        if not isinstance(step_id, str) or not step_id:
            raise WorkflowTransitionGrantPolicyError("workflow_transition_grant_policy_step_invalid")
        node = self._node(plan, step_id)
        if node is None:
            raise WorkflowTransitionGrantPolicyError("workflow_transition_grant_policy_step_not_planned")
        tools = _identity_tuple(getattr(node, "allowed_tools", ()), "allowed_tools")
        artifacts = _identity_tuple(
            (
                *_sequence(getattr(node, "input_artifacts", ())),
                *_sequence(getattr(node, "output_artifacts", ())),
            ),
            "allowed_artifacts",
        )
        return WorkflowTransitionGrantPlan(
            step_id=step_id,
            allowed_tools=tools,
            allowed_artifacts=artifacts,
            budgets=_budgets(getattr(node, "budget", None)),
            ttl_seconds=self._ttl(getattr(node, "budget", None)),
        )

    def _ttl(self, budget: Any) -> float:
        """Bound the grant to the work it authorizes, plus retry headroom."""

        timeout = getattr(budget, "timeout_seconds", None)
        attempts = getattr(budget, "max_attempts", None)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or float(timeout) <= 0
        ):
            return self._maximum_ttl_seconds
        span = float(timeout) * _DEFAULT_TTL_HEADROOM
        if not isinstance(attempts, bool) and isinstance(attempts, int) and attempts > 0:
            span = float(timeout) * float(attempts) * _DEFAULT_TTL_HEADROOM
        return max(_MIN_TTL_SECONDS, min(span, self._maximum_ttl_seconds))

    @staticmethod
    def _node(plan: Any, step_id: str) -> Any | None:
        nodes = getattr(plan, "nodes", None)
        # A str is a Sequence, so without this a malformed plan would iterate
        # into characters and report "step not planned" instead of "invalid".
        if isinstance(nodes, (str, bytes)) or not isinstance(nodes, Sequence):
            raise WorkflowTransitionGrantPolicyError("workflow_transition_grant_policy_plan_invalid")
        match: Any | None = None
        for node in nodes:
            if not hasattr(node, "node_id"):
                raise WorkflowTransitionGrantPolicyError("workflow_transition_grant_policy_plan_invalid")
            if match is None and str(node.node_id) == step_id:
                match = node
        return match


def _sequence(value: object) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise WorkflowTransitionGrantPolicyError("workflow_transition_grant_policy_sequence_invalid")
    return tuple(value)


def _identity_tuple(value: object, reason: str) -> tuple[str, ...]:
    items = _sequence(value)
    seen: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item or len(item) > 512:
            raise WorkflowTransitionGrantPolicyError(f"workflow_transition_grant_policy_{reason}_invalid")
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def _budgets(budget: Any) -> dict[str, int | float]:
    if budget is None:
        return {}
    values: dict[str, int | float] = {}
    for name in ("max_attempts", "max_tokens", "max_cost_micros"):
        raw = getattr(budget, name, None)
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise WorkflowTransitionGrantPolicyError("workflow_transition_grant_policy_budget_invalid")
        values[name] = raw
    timeout = getattr(budget, "timeout_seconds", None)
    if timeout is not None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or float(timeout) < 0
        ):
            raise WorkflowTransitionGrantPolicyError("workflow_transition_grant_policy_budget_invalid")
        values["timeout_seconds"] = float(timeout)
    return values


def grant_plan_to_mapping(value: WorkflowTransitionGrantPlan) -> Mapping[str, Any]:
    """Render the derived grant for the grant effect builder."""

    return {
        "allowed_artifacts": list(value.allowed_artifacts),
        "allowed_tools": list(value.allowed_tools),
        "budgets": dict(value.budgets),
        "ttl_seconds": value.ttl_seconds,
    }


__all__ = [
    "MAX_GRANT_TTL_SECONDS",
    "ExecutionPlanGrantPolicy",
    "WorkflowTransitionGrantPlan",
    "WorkflowTransitionGrantPolicyError",
    "grant_plan_to_mapping",
]
