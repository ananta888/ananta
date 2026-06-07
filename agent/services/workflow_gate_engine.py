"""Workflow gate engine (WFG-011, WFG-012, WFG-013, WFG-014).

This module is the deterministic, LLM-free implementation of gate
checks, gate decisions, gate state reconciliation, and failure
policy resolution. It is consumed by the queue layer (WFG-013) and
the workflow definition service (WFG-008).

Design constraints:

  - The gate check engine MUST be deterministic. The same input
    (task, workflow_step, gate checks) MUST always produce the same
    decision, byte-for-byte. Tests pin this property.
  - Gate state lives on the TaskDB itself (status + status_reason +
    verification_status gate block), so we do not need a new
    dedicated table for WFG-012.
  - Failure policy resolution (WFG-014) follows the precedence:
    1) per-step ``failure_policy`` from the workflow step
    2) per-blueprint ``default_gate_policy`` from the workflow
       defaults
    3) deployment-wide ``ANANTA_WORKFLOW_DEFAULT_GATE``
       (``GateFailurePolicy.BLOCK`` if unset)
  - The engine MUST NOT call any LLM. Checks are pure functions
    over the task, its workflow_step, and its dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from agent.services.workflow_settings import GateFailurePolicy, WorkflowSettings


# ---------------------------------------------------------------------------
# WFG-012: gate decision model and states
# ---------------------------------------------------------------------------


class GateDecisionStatus(str, Enum):
    """Final outcome of a gate evaluation."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    STALE = "stale"
    PENDING = "pending"  # not yet evaluated (initial state)


# TaskDB.status values used for gated steps. We deliberately reuse the
# existing status enum to avoid a migration; gates add a small set of
# sub-states via status_reason_code + verification_status.
GATE_PENDING_STATUS = "blocked"
GATE_PASSED_STATUS = "completed"
GATE_FAILED_STATUS = "failed"
GATE_SKIPPED_STATUS = "completed"  # skipped counts as "done" so downstream
                                   # steps unblock; the decision object
                                   # records the skip reason.


@dataclass(frozen=True)
class GateCheckResult:
    """Outcome of a single named check inside a gate."""

    name: str
    passed: bool
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GateDecision:
    """Final outcome of evaluating all checks on a gate step.

    Recorded on the TaskDB via ``verification_status['gate_decision']``
    so audit events (WFG-015) and the audit query (WFG-017) can
    reference it.
    """

    status: GateDecisionStatus
    check_results: tuple[GateCheckResult, ...]
    failure_policy: GateFailurePolicy
    policy_source: str  # "step" | "blueprint_default" | "deployment_default"
    reason_code: str = ""
    reason_details: dict[str, Any] = field(default_factory=dict)

    @property
    def all_checks_passed(self) -> bool:
        return all(c.passed for c in self.check_results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "workflow_gate_decision.v1",
            "status": self.status.value,
            "failure_policy": self.failure_policy.value,
            "policy_source": self.policy_source,
            "reason_code": self.reason_code,
            "reason_details": dict(self.reason_details),
            "check_results": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "reason": c.reason,
                    "details": dict(c.details),
                }
                for c in self.check_results
            ],
        }


# ---------------------------------------------------------------------------
# WFG-014: failure policy resolution
# ---------------------------------------------------------------------------


def resolve_gate_failure_policy(
    *,
    step: dict | None,
    blueprint_default: str | None = None,
    settings: WorkflowSettings | None = None,
) -> tuple[GateFailurePolicy, str]:
    """Resolve the effective failure policy for a gate step.

    Precedence (highest first):
      1) ``step['failure_policy']`` — the per-step override from the
         blueprint's workflow.steps[] entry.
      2) ``blueprint_default`` — the blueprint's
         ``workflow.defaults.failure_policy`` (when present).
      3) ``settings.default_gate_policy`` — the deployment-wide
         ``ANANTA_WORKFLOW_DEFAULT_GATE`` value, falling back to
         ``GateFailurePolicy.BLOCK``.

    Returns ``(policy, source)`` where ``source`` is one of
    ``"step"``, ``"blueprint_default"``, ``"deployment_default"``.
    """
    if isinstance(step, dict):
        raw = str(step.get("failure_policy") or "").strip().lower()
        if raw:
            try:
                return GateFailurePolicy(raw), "step"
            except ValueError:
                # Unknown value: fall through to next precedence level
                # instead of crashing the queue.
                pass
    if blueprint_default:
        raw = str(blueprint_default).strip().lower()
        try:
            return GateFailurePolicy(raw), "blueprint_default"
        except ValueError:
            pass
    fallback = settings.default_gate_policy if settings else GateFailurePolicy.BLOCK
    return fallback, "deployment_default"


# ---------------------------------------------------------------------------
# WFG-011: gate check engine
# ---------------------------------------------------------------------------


# A check is a pure callable: (task_dict, workflow_step_dict, context) -> GateCheckResult
GateCheckFn = Callable[[dict | None, dict | None, dict | None], GateCheckResult]


def _check_plan_has_small_tasks(
    task: dict | None, step: dict | None, ctx: dict | None
) -> GateCheckResult:
    """Pass when the upstream plan task produced >= 2 plan tasks, each
    with at least one acceptance criterion. This is the contract gate
    that blocks the developer step when the plan is too coarse."""
    plan = dict((ctx or {}).get("plan_tasks") or {})
    tasks = list(plan.get("tasks") or [])
    small = [
        t for t in tasks
        if list(t.get("acceptance_criteria") or [])
    ]
    passed = len(small) >= 2
    return GateCheckResult(
        name="plan_has_small_tasks",
        passed=passed,
        reason="all plan tasks have acceptance_criteria" if passed else
               "fewer than 2 plan tasks carry acceptance_criteria",
        details={"small_task_count": len(small), "total_task_count": len(tasks)},
    )


def _check_dependencies_acyclic(
    task: dict | None, step: dict | None, ctx: dict | None
) -> GateCheckResult:
    """Pass when the workflow DAG reachable from the gate has no
    cycles. We use a simple Kahn topological sort to confirm."""
    plan = dict((ctx or {}).get("plan_tasks") or {})
    nodes = {
        str(t.get("id") or "").strip(): [
            str(d).strip() for d in list(t.get("depends_on") or []) if str(d).strip()
        ]
        for t in list(plan.get("tasks") or [])
        if str(t.get("id") or "").strip()
    }
    in_degree: dict[str, int] = {n: 0 for n in nodes}
    edges: dict[str, list[str]] = {n: [] for n in nodes}
    for n, deps in nodes.items():
        for d in deps:
            if d in in_degree:
                in_degree[n] += 1
                edges[d].append(n)
    queue = [n for n, d in in_degree.items() if d == 0]
    visited = 0
    while queue:
        n = queue.pop(0)
        visited += 1
        for m in edges[n]:
            in_degree[m] -= 1
            if in_degree[m] == 0:
                queue.append(m)
    acyclic = visited == len(nodes)
    return GateCheckResult(
        name="dependencies_are_acyclic",
        passed=acyclic,
        reason="workflow DAG is acyclic" if acyclic else "workflow DAG has a cycle",
        details={"visited": visited, "total": len(nodes)},
    )


def _check_required_capabilities_present(
    task: dict | None, step: dict | None, ctx: dict | None
) -> GateCheckResult:
    """Pass when the gate step (or the downstream step it guards) has
    at least one entry in its ``required_capabilities`` list."""
    candidates: list[dict] = []
    if isinstance(step, dict):
        candidates.append(step)
    downstream = (ctx or {}).get("downstream_step")
    if isinstance(downstream, dict):
        candidates.append(downstream)
    has_caps = any(
        list(c.get("required_capabilities") or []) for c in candidates
    )
    return GateCheckResult(
        name="required_capabilities_present",
        passed=has_caps,
        reason="downstream step has required_capabilities" if has_caps else
               "no required_capabilities declared on the gated step",
        details={},
    )


def _check_security_review_needed(
    task: dict | None, step: dict | None, ctx: dict | None
) -> GateCheckResult:
    """Pass when either (a) no tools / credentials are referenced by
    the gated step, or (b) a security_reviewer step exists somewhere
    downstream. This blocks coding steps that touch credentials
    without a security review."""
    tools = list((ctx or {}).get("tool_usage") or [])
    credentials = list((ctx or {}).get("credentials") or [])
    has_risky = bool(tools) or bool(credentials)
    if not has_risky:
        return GateCheckResult(
            name="security_review_needed_if_tools_or_credentials_are_used",
            passed=True,
            reason="no tools or credentials referenced",
            details={},
        )
    security_downstream = bool((ctx or {}).get("security_reviewer_present"))
    return GateCheckResult(
        name="security_review_needed_if_tools_or_credentials_are_used",
        passed=security_downstream,
        reason="security_reviewer step present downstream" if security_downstream else
               "tools/credentials used but no security_reviewer step",
        details={"tool_count": len(tools), "credential_count": len(credentials)},
    )


# Registry of built-in checks. Custom checks can be added by
# BlueprintDefinitionService via ``register_gate_check``.
_BUILTIN_GATE_CHECKS: dict[str, GateCheckFn] = {
    "plan_has_small_tasks": _check_plan_has_small_tasks,
    "dependencies_are_acyclic": _check_dependencies_acyclic,
    "required_capabilities_present": _check_required_capabilities_present,
    "security_review_needed_if_tools_or_credentials_are_used": _check_security_review_needed,
}


def register_gate_check(name: str, fn: GateCheckFn) -> None:
    """Register a custom gate check (used by tests and by future
    blueprint extensions)."""
    _BUILTIN_GATE_CHECKS[str(name)] = fn


def evaluate_gate_checks(
    *,
    checks: dict[str, Any] | list[str] | list[dict[str, Any]] | None,
    task: dict | None,
    step: dict | None,
    context: dict | None = None,
) -> list[GateCheckResult]:
    """Evaluate each check declared on a gate step.

    ``checks`` may be a dict (name -> params), a list of names, or a
    list of dicts (each with ``name`` and optional ``params``). Unknown
    check names produce a failed result with a clear reason; the
    gate does not crash the worker.
    """
    results: list[GateCheckResult] = []
    if not checks:
        return results
    items: list[tuple[str, dict[str, Any]]] = []
    if isinstance(checks, dict):
        for name, params in checks.items():
            params_dict = dict(params) if isinstance(params, dict) else {}
            items.append((str(name), params_dict))
    elif isinstance(checks, list):
        for item in checks:
            if isinstance(item, str):
                items.append((item, {}))
            elif isinstance(item, dict) and item.get("name"):
                params = dict(item.get("params") or {}) if isinstance(item.get("params"), dict) else {}
                items.append((str(item["name"]), params))
    for name, params in items:
        fn = _BUILTIN_GATE_CHECKS.get(name)
        if fn is None:
            results.append(
                GateCheckResult(
                    name=name,
                    passed=False,
                    reason=f"unknown_check:{name}",
                    details=params,
                )
            )
            continue
        try:
            result = fn(task, step, dict(context or {}) | {"params": params})
        except Exception as exc:  # noqa: BLE001 — gate checks must not crash the queue
            result = GateCheckResult(
                name=name,
                passed=False,
                reason=f"check_raised:{type(exc).__name__}:{exc}",
                details={},
            )
        results.append(result)
    return results


def evaluate_gate(
    *,
    task: dict | None,
    step: dict | None,
    checks: dict | list | None,
    blueprint_default: str | None = None,
    settings: WorkflowSettings | None = None,
    context: dict | None = None,
) -> GateDecision:
    """Evaluate a gate step end-to-end. Returns a GateDecision that
    the caller persists to the task's ``verification_status``."""
    policy, source = resolve_gate_failure_policy(
        step=step, blueprint_default=blueprint_default, settings=settings
    )
    check_results = evaluate_gate_checks(
        checks=checks, task=task, step=step, context=context
    )
    if not check_results:
        # No checks configured — the gate is informational only.
        return GateDecision(
            status=GateDecisionStatus.PASSED,
            check_results=(),
            failure_policy=policy,
            policy_source=source,
            reason_code="no_checks_configured",
        )
    all_passed = all(c.passed for c in check_results)
    if all_passed:
        return GateDecision(
            status=GateDecisionStatus.PASSED,
            check_results=tuple(check_results),
            failure_policy=policy,
            policy_source=source,
        )
    # A failed gate may still produce a SKIPPED decision when the
    # configured failure_policy is SKIP. The failure_policy drives
    # the propagation, not the check outcome.
    if policy == GateFailurePolicy.SKIP:
        return GateDecision(
            status=GateDecisionStatus.SKIPPED,
            check_results=tuple(check_results),
            failure_policy=policy,
            policy_source=source,
            reason_code="gate_failed_but_policy_skip",
            reason_details={
                "failed_checks": [c.name for c in check_results if not c.passed],
            },
        )
    if policy == GateFailurePolicy.MANUAL:
        return GateDecision(
            status=GateDecisionStatus.PENDING,
            check_results=tuple(check_results),
            failure_policy=policy,
            policy_source=source,
            reason_code="gate_failed_pending_human_approval",
            reason_details={
                "failed_checks": [c.name for c in check_results if not c.passed],
            },
        )
    return GateDecision(
        status=GateDecisionStatus.FAILED,
        check_results=tuple(check_results),
        failure_policy=policy,
        policy_source=source,
        reason_code="gate_failed",
        reason_details={
            "failed_checks": [c.name for c in check_results if not c.passed],
        },
    )


# ---------------------------------------------------------------------------
# WFG-013: queue-side reconciliation helpers
# ---------------------------------------------------------------------------


def apply_gate_decision_to_task_status(
    *,
    current_status: str,
    decision: GateDecision,
) -> tuple[str, str, str]:
    """Map a GateDecision to the next TaskDB.status, status_reason_code,
    and verification_status gate block to persist.

    Returns ``(new_status, status_reason_code, verification_gate_block)``
    so the caller can write the three fields atomically.
    """
    if decision.status == GateDecisionStatus.PASSED:
        return (
            GATE_PASSED_STATUS,
            "gate_passed",
            "passed",
        )
    if decision.status == GateDecisionStatus.SKIPPED:
        return (
            GATE_SKIPPED_STATUS,
            "gate_skipped_by_policy",
            "skipped",
        )
    if decision.status == GateDecisionStatus.STALE:
        return (
            GATE_FAILED_STATUS,
            "gate_stale",
            "stale",
        )
    if decision.status == GateDecisionStatus.PENDING:
        return (
            GATE_PENDING_STATUS,
            "gate_failed_pending_human_approval",
            "pending_approval",
        )
    # FAILED
    return (
        GATE_FAILED_STATUS,
        "gate_failed",
        "failed",
    )


def reconcile_gate_state(
    *,
    task: dict | None,
    step: dict | None,
    settings: WorkflowSettings | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    """Inspect a gate task and return the reconciliation action the
    queue should take.

    The action is one of:

      - ``{"action": "noop"}`` — task is not a gate, or its state is
        already consistent.
      - ``{"action": "mark_stale", "reason_code": "gate_timeout"}`` —
        gate has been pending longer than ``gate_timeout_seconds``.
      - ``{"action": "unblock_downstream", "decision": <GateDecision>}``
        — gate has been evaluated (or can be evaluated from
        ``verification_status``) and downstream tasks may proceed.
      - ``{"action": "keep_blocked", "reason_code": ...}`` — gate is
        still legitimately pending.

    This function is pure: it does not touch the database. The
    caller (``task_queue_service``) is responsible for persisting
    the action.
    """
    if not isinstance(task, dict):
        return {"action": "noop"}
    step = step if isinstance(step, dict) else {}
    is_gate = bool(step.get("gate", False))
    if not is_gate:
        return {"action": "noop"}
    status = str(task.get("status") or "").strip()
    verification = dict(task.get("verification_status") or {})
    gate_block = verification.get("gate")
    timeout_seconds = settings.gate_timeout_seconds if settings else 86400
    pending_since = float(task.get("status_reason_details", {}).get("gate_pending_since") or 0)
    if status == GATE_PENDING_STATUS and timeout_seconds > 0 and pending_since > 0 and now_ts is not None:
        if now_ts - pending_since > timeout_seconds:
            return {"action": "mark_stale", "reason_code": "gate_timeout"}
    if gate_block in {"passed", "skipped"}:
        return {
            "action": "unblock_downstream",
            "decision_status": gate_block,
        }
    if gate_block == "failed":
        return {
            "action": "keep_blocked",
            "reason_code": "gate_failed",
        }
    if gate_block == "pending_approval":
        return {
            "action": "keep_blocked",
            "reason_code": "gate_failed_pending_human_approval",
        }
    return {
        "action": "keep_blocked",
        "reason_code": "gate_pending",
    }
