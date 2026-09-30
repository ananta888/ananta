"""Authoritative release-binding evaluation for Recovery child tasks.

Answers one question: does the persisted plan/source/goal ownership still
authorize dispatching this Recovery child?  Locks, leases and transport are
owned by ``RecoveryDispatchGateService``.
"""

from __future__ import annotations

import hmac
from typing import Any, Callable

from agent.common.recovery_dispatch_contract import (
    RecoveryDispatchGateDecision,
    _mapping,
    _value,
)
from agent.services.recovery_dispatch_gate_policy import (
    DISPATCHABLE_RECOVERY_STATUSES,
    SUCCESSFUL_DEPENDENCY_STATUSES,
    TERMINAL_GOAL_STATUSES,
    TERMINAL_TASK_STATUSES,
    is_recovery_child,
    is_recovery_source,
)
from agent.services.recovery_plan_contract import (
    calculate_recovery_materialization_inputs_digest,
    calculate_recovery_plan_digest,
    calculate_recovery_task_payload_digest,
)


def _expected_node_payload(child_node: Any) -> dict[str, Any]:
    node_rationale = _mapping(
        getattr(child_node, "rationale", None)
    )
    return {
        "title": str(
            getattr(child_node, "title", "") or ""
        ),
        "description": str(
            getattr(child_node, "description", "") or ""
        ),
        "priority": str(
            getattr(child_node, "priority", "") or ""
        ),
        "task_kind": str(
            node_rationale.get("task_kind") or ""
        ),
        "retrieval_intent": str(
            node_rationale.get("retrieval_intent") or ""
        ),
        "required_context_scope": str(
            node_rationale.get("required_context_scope") or ""
        ),
        "preferred_bundle_mode": str(
            node_rationale.get("preferred_bundle_mode") or ""
        ),
        "required_capabilities": list(
            node_rationale.get("required_capabilities") or []
        ),
        "verification_spec": _mapping(
            getattr(child_node, "verification_spec", None)
        ),
    }


def _actual_node_payload(task: Any) -> dict[str, Any]:
    return {
        "title": str(_value(task, "title") or ""),
        "description": str(
            _value(task, "description") or ""
        ),
        "priority": str(_value(task, "priority") or ""),
        "task_kind": str(
            _value(task, "task_kind") or ""
        ),
        "retrieval_intent": str(
            _value(task, "retrieval_intent") or ""
        ),
        "required_context_scope": str(
            _value(task, "required_context_scope") or ""
        ),
        "preferred_bundle_mode": str(
            _value(task, "preferred_bundle_mode") or ""
        ),
        "required_capabilities": list(
            _value(task, "required_capabilities") or []
        ),
        "verification_spec": _mapping(
            _value(task, "verification_spec")
        ),
    }


def _find_child_node(task: Any, nodes: list[Any]) -> Any:
    return next(
        (
            node
            for node in nodes
            if str(
                getattr(node, "materialized_task_id", "")
                or ""
            )
            == str(_value(task, "id") or "")
            and str(getattr(node, "id", "") or "")
            == str(_value(task, "plan_node_id") or "")
        ),
        None,
    )


def _expected_dependencies(
    child_node: Any,
    nodes: list[Any],
) -> list[str]:
    task_ids_by_node_key = {
        str(getattr(node, "node_key", "") or ""): str(
            getattr(node, "materialized_task_id", "") or ""
        )
        for node in nodes
    }
    return [
        task_ids_by_node_key[str(node_key)]
        for node_key in list(
            getattr(child_node, "depends_on", None) or []
        )
        if task_ids_by_node_key.get(str(node_key))
    ]


def _release_epoch_bindings_match(
    *,
    rationale: dict[str, Any],
    child_release: dict[str, Any],
    source_recovery: dict[str, Any],
    release_epoch: str,
    plan_id: str,
    source_task_id: str,
    goal_id: str,
    plan_team_id: str,
    approval_id: str,
    recovery_key: str,
) -> bool:
    return bool(
        str(child_release.get("release_epoch") or "")
        == release_epoch
        and str(child_release.get("plan_id") or "")
        == plan_id
        and str(child_release.get("source_task_id") or "")
        == source_task_id
        and str(child_release.get("goal_id") or "")
        == goal_id
        and str(child_release.get("team_id") or "")
        == plan_team_id
        and str(source_recovery.get("release_epoch") or "")
        == release_epoch
        and str(
            rationale.get(
                "materialization_release_source_task_id"
            )
            or ""
        )
        == source_task_id
        and str(
            rationale.get(
                "materialization_release_goal_id"
            )
            or ""
        )
        == goal_id
        and str(
            rationale.get(
                "materialization_release_team_id"
            )
            or ""
        )
        == plan_team_id
        and approval_id
        and approval_id
        == str(
            source_recovery.get(
                "approval_request_id"
            )
            or ""
        )
        == str(
            child_release.get(
                "approval_request_id"
            )
            or ""
        )
        and recovery_key
        and recovery_key
        == str(
            source_recovery.get("recovery_key") or ""
        )
        == str(
            child_release.get("recovery_key") or ""
        )
    )


def evaluate_recovery_release(
    task: Any,
    *,
    resolve_repos: Callable[[], Any],
    allow_terminal_task: bool = False,
) -> RecoveryDispatchGateDecision:
    """Evaluate ``task`` against its persisted release ownership.

    ``resolve_repos`` is only invoked for Recovery children, preserving the
    original lazy repository resolution for ordinary tasks.
    """

    if task is None:
        return RecoveryDispatchGateDecision(
            False,
            "task_not_found",
        )
    if is_recovery_source(task):
        return RecoveryDispatchGateDecision(
            False,
            "recovery_source_not_executable",
            source_task_id=str(_value(task, "id") or "")
            or None,
        )
    if not is_recovery_child(task):
        return RecoveryDispatchGateDecision(
            True,
            "not_recovery_child",
        )

    repos = resolve_repos()
    plan_id = str(_value(task, "plan_id") or "").strip()
    source_task_id = str(
        _value(task, "source_task_id") or ""
    ).strip()
    goal_id = str(_value(task, "goal_id") or "").strip()
    child_team_id = str(
        _value(task, "team_id") or ""
    ).strip()
    child_status = str(
        _value(task, "status") or ""
    ).strip().lower()
    if (
        child_status in TERMINAL_TASK_STATUSES
        and not allow_terminal_task
    ):
        return RecoveryDispatchGateDecision(
            False,
            "recovery_dispatch_task_terminal",
            source_task_id=source_task_id or None,
            plan_id=plan_id or None,
        )
    if (
        child_status not in DISPATCHABLE_RECOVERY_STATUSES
        and not (
            allow_terminal_task
            and child_status in TERMINAL_TASK_STATUSES
        )
    ):
        return RecoveryDispatchGateDecision(
            False,
            "recovery_dispatch_status_not_dispatchable",
            source_task_id=source_task_id or None,
            plan_id=plan_id or None,
        )
    if not all(
        (plan_id, source_task_id, goal_id, child_team_id)
    ):
        return RecoveryDispatchGateDecision(
            False,
            "recovery_dispatch_binding_incomplete",
            source_task_id=source_task_id or None,
            plan_id=plan_id or None,
        )

    def denied(
        reason_code: str,
        *,
        release_epoch: str | None = None,
    ) -> RecoveryDispatchGateDecision:
        return RecoveryDispatchGateDecision(
            False,
            reason_code,
            source_task_id=source_task_id,
            plan_id=plan_id,
            release_epoch=release_epoch,
        )

    plan = repos.plan_repo.get_by_id(plan_id)
    source = repos.task_repo.get_by_id(source_task_id)
    goal = repos.goal_repo.get_by_id(goal_id)
    if plan is None or source is None or goal is None:
        return denied("recovery_dispatch_owner_missing")

    rationale = _mapping(getattr(plan, "rationale", None))
    if str(
        rationale.get("materialization_inputs_digest") or ""
    ) != calculate_recovery_materialization_inputs_digest(goal):
        return denied("recovery_materialization_inputs_changed")
    nodes = list(
        repos.plan_node_repo.get_by_plan_id(plan_id) or []
    )
    current_plan_digest = calculate_recovery_plan_digest(
        plan,
        nodes,
    )
    if (
        not nodes
        or str(rationale.get("plan_digest") or "")
        != current_plan_digest
    ):
        return denied("recovery_dispatch_plan_digest_mismatch")
    child_node = _find_child_node(task, nodes)
    if child_node is None:
        return denied("recovery_dispatch_plan_node_mismatch")
    if _actual_node_payload(task) != _expected_node_payload(child_node):
        return denied("recovery_dispatch_plan_node_payload_mismatch")
    expected_dependencies = _expected_dependencies(child_node, nodes)
    if list(_value(task, "depends_on") or []) != (
        expected_dependencies
    ):
        return denied("recovery_dispatch_dependency_binding_mismatch")
    for dependency_id in expected_dependencies:
        dependency = repos.task_repo.get_by_id(dependency_id)
        dependency_status = str(
            _value(dependency, "status") or ""
        ).strip().lower()
        if (
            dependency is None
            or dependency_status
            not in SUCCESSFUL_DEPENDENCY_STATUSES
        ):
            return denied("recovery_dispatch_dependency_incomplete")
    source_recovery = _mapping(
        _mapping(
            _value(source, "status_reason_details")
        ).get("model_recovery")
    )
    child_release = _mapping(
        _mapping(
            _value(task, "status_reason_details")
        ).get("model_recovery_release")
    )
    approved_payload_digest = str(
        child_release.get("task_payload_digest") or ""
    )
    if (
        not approved_payload_digest
        or not hmac.compare_digest(
            approved_payload_digest,
            calculate_recovery_task_payload_digest(task),
        )
    ):
        return denied("recovery_dispatch_payload_digest_mismatch")
    release_state = str(
        rationale.get("materialization_release_state") or ""
    ).strip()
    release_epoch = str(
        rationale.get("materialization_release_epoch") or ""
    ).strip()
    source_status = str(
        _value(source, "status") or ""
    ).strip().lower()
    goal_status = str(
        _value(goal, "status") or ""
    ).strip().lower()
    source_team_id = str(
        _value(source, "team_id") or ""
    ).strip()
    goal_team_id = str(
        _value(goal, "team_id") or ""
    ).strip()
    plan_team_id = str(
        rationale.get("team_id") or ""
    ).strip()
    approval_id = str(
        rationale.get(
            "materialization_release_approval_id"
        )
        or ""
    ).strip()
    recovery_key = str(
        rationale.get("recovery_key") or ""
    ).strip()

    if (
        source_status in TERMINAL_TASK_STATUSES
        or goal_status in TERMINAL_GOAL_STATUSES
    ):
        return denied(
            "recovery_dispatch_owner_terminal",
            release_epoch=release_epoch or None,
        )
    if release_state not in {"committed", "completed"}:
        return denied(
            "recovery_release_not_committed",
            release_epoch=release_epoch or None,
        )
    if not (
        str(_value(plan, "goal_id") or "") == goal_id
        and str(
            _value(plan, "status") or ""
        ).strip().lower()
        == "materialized"
        and str(_value(source, "goal_id") or "") == goal_id
        and str(rationale.get("source_task_id") or "")
        == source_task_id
        and str(source_recovery.get("plan_id") or "") == plan_id
        and source_status == "blocked_by_dependency"
        and plan_team_id
        and plan_team_id
        == child_team_id
        == source_team_id
        == goal_team_id
    ):
        return denied(
            "recovery_dispatch_binding_mismatch",
            release_epoch=release_epoch or None,
        )

    if release_epoch:
        if not _release_epoch_bindings_match(
            rationale=rationale,
            child_release=child_release,
            source_recovery=source_recovery,
            release_epoch=release_epoch,
            plan_id=plan_id,
            source_task_id=source_task_id,
            goal_id=goal_id,
            plan_team_id=plan_team_id,
            approval_id=approval_id,
            recovery_key=recovery_key,
        ):
            return denied(
                "recovery_release_epoch_mismatch",
                release_epoch=release_epoch,
            )
    elif child_release:
        # New-format children may never fall back to the legacy path.
        return denied("recovery_release_epoch_missing")

    return RecoveryDispatchGateDecision(
        True,
        (
            "recovery_release_gate_valid"
            if release_epoch
            else "recovery_release_legacy_completed"
        ),
        source_task_id=source_task_id,
        plan_id=plan_id,
        release_epoch=release_epoch or None,
    )
