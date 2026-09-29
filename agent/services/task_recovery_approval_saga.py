"""Approval request and stale-approval refresh saga of Hub recovery planning.

Split out of ``task_recovery_planning_service`` (SRP): requesting the exact
Hub materialization approval for a recovery plan, parking the source task
while it waits, and refreshing a stale approval binding in a crash-safe saga.
``TaskRecoveryPlanningService`` keeps the method names and delegates here with
itself as ``service``.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

from agent.services.approval_auto_grant_policy import RECOVERY_MATERIALIZE_TOOL
from agent.services.recovery_plan_contract import calculate_recovery_materialization_inputs_digest
from agent.services.task_recovery_planning_rules import (
    _TERMINAL_GOAL_STATUSES,
    _TERMINAL_TASK_STATUSES,
    RECOVERY_STATE_SCHEMA,
)
from agent.services.task_recovery_planning_values import mapping as _mapping

log = logging.getLogger("agent.services.task_recovery_planning_service")


def request_materialization_approval(
    service,
    *,
    repos: Any,
    plan: Any,
    nodes: list[Any],
    goal_id: str,
    source_task_id: str,
    recovery_key: str,
    policy_hash: str,
    team_id: str,
) -> tuple[Any, str]:
    goal = repos.goal_repo.get_by_id(goal_id)
    if goal is None:
        raise RuntimeError("recovery_goal_not_found")
    materialization_inputs_digest = (
        calculate_recovery_materialization_inputs_digest(
            goal
        )
    )
    plan.rationale = {
        **_mapping(getattr(plan, "rationale", None)),
        "materialization_inputs_digest": (
            materialization_inputs_digest
        ),
    }
    plan.updated_at = time.time()
    plan = repos.plan_repo.save(plan)
    plan_digest = service._plan_digest(plan, nodes)
    approval = service._approval_service().create_pending_request(
        task_id=source_task_id,
        goal_id=goal_id,
        tenant_id=str(getattr(goal, "tenant_id", "") or "").strip()
        or None,
        project_id=str(getattr(goal, "project_id", "") or "").strip()
        or None,
        organization_id=str(
            getattr(goal, "organization_id", "") or ""
        ).strip()
        or None,
        trace_id=str(getattr(plan, "trace_id", "") or "") or None,
        tool_name=RECOVERY_MATERIALIZE_TOOL,
        arguments={
            "goal_id": goal_id,
            "plan_id": str(getattr(plan, "id", "") or ""),
            "plan_digest": plan_digest,
            "policy_hash": policy_hash,
            "recovery_key": recovery_key,
            "source_task_id": source_task_id,
            "team_id": team_id,
        },
        target_fingerprint=plan_digest,
        risk_class="task_materialization",
        governance_mode="balanced",
        scope={
            "approval_class": "task_materialization",
            "source": "model_context_recovery",
            "reason_code": "model_or_strategy_exhausted",
            "goal_id": goal_id,
            "plan_id": str(getattr(plan, "id", "") or ""),
            "source_task_id": source_task_id,
            "recovery_key": recovery_key,
            "team_id": team_id,
            "tenant_id": str(
                getattr(goal, "tenant_id", "") or ""
            ).strip(),
            "project_id": str(
                getattr(goal, "project_id", "") or ""
            ).strip(),
            "organization_id": str(
                getattr(goal, "organization_id", "") or ""
            ).strip(),
        },
    )
    plan.rationale = {
        **_mapping(getattr(plan, "rationale", None)),
        "approval_request_id": approval.id,
        "plan_digest": plan_digest,
        "approval_state": str(approval.status or "pending"),
        "team_id": team_id,
    }
    plan.updated_at = time.time()
    repos.plan_repo.save(plan)
    return approval, plan_digest


def mark_source_waiting_for_approval(
    service,
    *,
    source_task: Any,
    plan_id: str,
    approval_request_id: str,
    recovery_key: str,
    node_count: int,
    team_id: str,
) -> bool:
    state = {
        "schema": RECOVERY_STATE_SCHEMA,
        "status": "pending_approval",
        "plan_id": plan_id,
        "approval_request_id": approval_request_id,
        "recovery_key": recovery_key,
        "recovery_depth": 1,
        "node_count": max(0, int(node_count)),
        "team_id": team_id,
    }
    current_status = str(getattr(source_task, "status", "") or "").strip().lower()
    if not current_status or current_status in _TERMINAL_TASK_STATUSES:
        return False
    task_id = str(getattr(source_task, "id", "") or "")
    current_state = _mapping(
        _mapping(
            getattr(
                source_task,
                "status_reason_details",
                None,
            )
        ).get("model_recovery")
    )
    current_approval_id = str(
        current_state.get("approval_request_id") or ""
    ).strip()
    authority = contextlib.nullcontext()
    if (
        current_approval_id
        and current_approval_id != approval_request_id
    ):
        from agent.common.recovery_source_approval_rebind_write_boundary import (
            authorize_recovery_source_approval_rebind_write,
        )

        authority = (
            authorize_recovery_source_approval_rebind_write(
                task_id=task_id,
                current_state=current_state,
                proposed_state=state,
            )
        )
    with authority:
        return service._conditional_update_task(
            task_id,
            "waiting_for_review",
            expected_statuses={current_status},
            force=False,
            verification_status={
                **_mapping(
                    getattr(
                        source_task,
                        "verification_status",
                        None,
                    )
                ),
                "model_recovery": state,
            },
            status_reason_code=(
                "model_recovery_plan_pending_approval"
            ),
            status_reason_details={
                **_mapping(
                    getattr(
                        source_task,
                        "status_reason_details",
                        None,
                    )
                ),
                "model_recovery": state,
            },
            event_type=(
                "task_recovery_plan_pending_approval"
            ),
            event_actor="hub_recovery_planner",
            event_details={
                "plan_id": plan_id,
                "approval_request_id": (
                    approval_request_id
                ),
            },
        )


def complete_approval_refresh_saga(
    service,
    *,
    repos: Any,
    plan_id: str,
    goal_id: str,
    source_task_id: str,
    recovery_key: str,
    team_id: str,
    node_count: int,
    stale_approval_id: str,
    refreshed_approval_id: str,
    refreshed_digest: str,
) -> dict[str, Any]:
    """Finish source rebind, stale-grant consume, and saga completion."""

    with (
        service._distributed_source_lock(
            source_task_id
        ) as source_lock_acquired,
        service._source_mutation_lock(source_task_id),
    ):
        if not source_lock_acquired:
            return {
                "status": "ignored",
                "reason_code": "recovery_action_in_progress",
                "plan_id": plan_id,
            }
        source_task = repos.task_repo.get_by_id(source_task_id)
        goal = repos.goal_repo.get_by_id(goal_id)
        if (
            source_task is None
            or goal is None
            or service._is_terminal(
                source_task,
                _TERMINAL_TASK_STATUSES,
            )
            or service._is_terminal(
                goal,
                _TERMINAL_GOAL_STATUSES,
            )
            or not service._team_binding_matches(
                source_task=source_task,
                goal=goal,
                expected_team_id=team_id,
            )
        ):
            return {
                "status": "stopped",
                "reason_code": (
                    "recovery_team_binding_changed"
                    if source_task is not None
                    and goal is not None
                    and not service._team_binding_matches(
                        source_task=source_task,
                        goal=goal,
                        expected_team_id=team_id,
                    )
                    else "recovery_refresh_owner_terminal"
                ),
                "plan_id": plan_id,
            }

        source_recovery = _mapping(
            _mapping(
                getattr(
                    source_task,
                    "status_reason_details",
                    None,
                )
            ).get("model_recovery")
        )
        current_approval_id = str(
            source_recovery.get("approval_request_id") or ""
        )
        source_owned = bool(
            str(source_recovery.get("plan_id") or "") == plan_id
            and str(source_recovery.get("recovery_key") or "")
            == recovery_key
            and service._stored_team_binding_matches(
                source_recovery,
                team_id,
            )
        )
        source_empty = not any(
            str(source_recovery.get(key) or "").strip()
            for key in (
                "plan_id",
                "recovery_key",
                "approval_request_id",
            )
        )
        if (
            current_approval_id != refreshed_approval_id
            and not (
                source_empty
                or (
                    source_owned
                    and current_approval_id
                    == stale_approval_id
                )
            )
        ):
            return {
                "status": "failed",
                "reason_code": (
                    "recovery_refresh_source_binding_conflict"
                ),
                "plan_id": plan_id,
            }
        if current_approval_id != refreshed_approval_id:
            transitioned = service._mark_source_waiting_for_approval(
                source_task=source_task,
                plan_id=plan_id,
                approval_request_id=refreshed_approval_id,
                recovery_key=recovery_key,
                node_count=node_count,
                team_id=team_id,
            )
            if not transitioned:
                return {
                    "status": "failed",
                    "reason_code": (
                        "recovery_source_transition_conflict"
                    ),
                    "plan_id": plan_id,
                }

        confirmed_source = repos.task_repo.get_by_id(
            source_task_id
        )
        confirmed_recovery = _mapping(
            _mapping(
                getattr(
                    confirmed_source,
                    "status_reason_details",
                    None,
                )
            ).get("model_recovery")
        )
        if not (
            confirmed_source is not None
            and str(
                confirmed_recovery.get("plan_id") or ""
            )
            == plan_id
            and str(
                confirmed_recovery.get(
                    "approval_request_id"
                )
                or ""
            )
            == refreshed_approval_id
            and str(
                confirmed_recovery.get("recovery_key") or ""
            )
            == recovery_key
            and service._stored_team_binding_matches(
                confirmed_recovery,
                team_id,
            )
        ):
            return {
                "status": "failed",
                "reason_code": (
                    "recovery_refresh_source_confirmation_failed"
                ),
                "plan_id": plan_id,
            }

    with service._plan_mutation_lock(plan_id) as acquired:
        if not acquired:
            return {
                "status": "ignored",
                "reason_code": "plan_mutation_in_progress",
                "plan_id": plan_id,
            }
        plan = repos.plan_repo.get_by_id(plan_id)
        if plan is None:
            return {
                "status": "failed",
                "reason_code": "approval_target_not_found",
            }
        rationale = _mapping(getattr(plan, "rationale", None))
        saga = _mapping(rationale.get("approval_refresh"))
        if not (
            str(saga.get("stale_approval_request_id") or "")
            == stale_approval_id
            and str(
                saga.get("refreshed_approval_request_id")
                or ""
            )
            == refreshed_approval_id
            and str(
                rationale.get("approval_request_id") or ""
            )
            == refreshed_approval_id
        ):
            return {
                "status": "failed",
                "reason_code": (
                    "recovery_refresh_plan_binding_conflict"
                ),
                "plan_id": plan_id,
            }
        plan.rationale = {
            **rationale,
            "approval_state": "refresh_source_bound",
            "approval_refresh": {
                **saga,
                "state": "source_bound",
                "updated_at": time.time(),
            },
        }
        plan.updated_at = time.time()
        repos.plan_repo.save(plan)

    try:
        consumed = service._approval_service().consume_request(
            stale_approval_id
        )
    except Exception:
        log.exception(
            "stale recovery approval consume failed for %s",
            stale_approval_id,
        )
        consumed = None
    if consumed is None:
        return {
            "status": "failed",
            "reason_code": "approval_consume_failed",
            "plan_id": plan_id,
        }

    with service._plan_mutation_lock(plan_id) as acquired:
        if not acquired:
            return {
                "status": "ignored",
                "reason_code": "plan_mutation_in_progress",
                "plan_id": plan_id,
            }
        plan = repos.plan_repo.get_by_id(plan_id)
        if plan is None:
            return {
                "status": "failed",
                "reason_code": "approval_target_not_found",
            }
        rationale = _mapping(getattr(plan, "rationale", None))
        saga = _mapping(rationale.get("approval_refresh"))
        if (
            str(saga.get("stale_approval_request_id") or "")
            != stale_approval_id
            or str(
                saga.get("refreshed_approval_request_id")
                or ""
            )
            != refreshed_approval_id
        ):
            return {
                "status": "failed",
                "reason_code": (
                    "recovery_refresh_plan_binding_conflict"
                ),
                "plan_id": plan_id,
            }
        plan.status = "pending_approval"
        plan.rationale = {
            **rationale,
            "approval_state": "pending",
            "approval_refresh": {
                **saga,
                "state": "completed",
                "updated_at": time.time(),
            },
        }
        plan.updated_at = time.time()
        repos.plan_repo.save(plan)

    return {
        "status": "pending_approval",
        "reason_code": "recovery_plan_digest_refreshed",
        "plan_id": plan_id,
        "approval_request_id": refreshed_approval_id,
        "plan_digest": refreshed_digest,
        "approval_status": "consumed",
    }


def refresh_stale_plan_approval(
    service,
    *,
    repos: Any,
    plan_id: str,
    goal_id: str,
    source_task_id: str,
    recovery_key: str,
    policy_hash: str,
    team_id: str,
    stale_approval_id: str,
) -> dict[str, Any]:
    """Create or resume the durable stale-digest replacement saga."""

    with service._plan_mutation_lock(plan_id) as acquired:
        if not acquired:
            return {
                "status": "ignored",
                "reason_code": "plan_mutation_in_progress",
                "plan_id": plan_id,
            }
        plan = repos.plan_repo.get_by_id(plan_id)
        nodes = repos.plan_node_repo.get_by_plan_id(plan_id)
        if plan is None or not nodes:
            return {
                "status": "failed",
                "reason_code": "approval_target_not_found",
            }
        rationale = _mapping(getattr(plan, "rationale", None))
        current_digest = service._plan_digest(plan, nodes)
        saga = _mapping(rationale.get("approval_refresh"))
        saga_matches = (
            str(saga.get("stale_approval_request_id") or "")
            == stale_approval_id
            and service._stored_team_binding_matches(
                saga,
                team_id,
            )
        )
        refreshed_approval_id = (
            str(
                saga.get("refreshed_approval_request_id")
                or ""
            )
            if saga_matches
            else ""
        )
        refreshed_digest = (
            str(saga.get("refreshed_plan_digest") or "")
            if saga_matches
            else ""
        )
        if (
            saga_matches
            and refreshed_digest
            and refreshed_digest != current_digest
        ):
            return {
                "status": "failed",
                "reason_code": (
                    "recovery_refresh_plan_changed_again"
                ),
                "plan_id": plan_id,
            }
        if saga_matches and not refreshed_approval_id:
            candidate_id = str(
                rationale.get("approval_request_id") or ""
            )
            if candidate_id != stale_approval_id:
                get_request = getattr(
                    service._approval_service(),
                    "get_request",
                    None,
                )
                candidate = (
                    get_request(candidate_id)
                    if callable(get_request)
                    else None
                )
                candidate_args = _mapping(
                    getattr(
                        candidate,
                        "canonical_arguments",
                        None,
                    )
                )
                if (
                    candidate is not None
                    and str(
                        getattr(
                            candidate,
                            "target_fingerprint",
                            "",
                        )
                        or ""
                    )
                    == current_digest
                    and service._stored_team_binding_matches(
                        candidate_args,
                        team_id,
                    )
                ):
                    refreshed_approval_id = candidate_id
                    refreshed_digest = current_digest

        if not saga_matches:
            plan.status = "pending_approval"
            plan.rationale = {
                **rationale,
                "approval_state": (
                    "refreshing_stale_plan_digest"
                ),
                "approval_refresh": {
                    "schema": (
                        "ananta.recovery_approval_refresh.v1"
                    ),
                    "state": "creating",
                    "stale_approval_request_id": (
                        stale_approval_id
                    ),
                    "refreshed_approval_request_id": "",
                    "refreshed_plan_digest": current_digest,
                    "team_id": team_id,
                    "updated_at": time.time(),
                },
            }
            plan.updated_at = time.time()
            plan = repos.plan_repo.save(plan)
            refreshed_digest = current_digest

        if not refreshed_approval_id:
            refreshed, refreshed_digest = (
                service._request_materialization_approval(
                    repos=repos,
                    plan=plan,
                    nodes=nodes,
                    goal_id=goal_id,
                    source_task_id=source_task_id,
                    recovery_key=recovery_key,
                    policy_hash=policy_hash,
                    team_id=team_id,
                )
            )
            refreshed_approval_id = str(
                getattr(refreshed, "id", "") or ""
            )

        plan = repos.plan_repo.get_by_id(plan_id)
        if plan is None:
            return {
                "status": "failed",
                "reason_code": "approval_target_not_found",
            }
        rationale = _mapping(getattr(plan, "rationale", None))
        saga = _mapping(rationale.get("approval_refresh"))
        plan.status = "pending_approval"
        plan.rationale = {
            **rationale,
            "approval_request_id": refreshed_approval_id,
            "plan_digest": refreshed_digest,
            "approval_state": "refresh_approval_bound",
            "approval_refresh": {
                **saga,
                "schema": (
                    "ananta.recovery_approval_refresh.v1"
                ),
                "state": "approval_bound",
                "stale_approval_request_id": (
                    stale_approval_id
                ),
                "refreshed_approval_request_id": (
                    refreshed_approval_id
                ),
                "refreshed_plan_digest": refreshed_digest,
                "team_id": team_id,
                "updated_at": time.time(),
            },
        }
        plan.updated_at = time.time()
        repos.plan_repo.save(plan)
        node_count = len(nodes)

    return service._complete_approval_refresh_saga(
        repos=repos,
        plan_id=plan_id,
        goal_id=goal_id,
        source_task_id=source_task_id,
        recovery_key=recovery_key,
        team_id=team_id,
        node_count=node_count,
        stale_approval_id=stale_approval_id,
        refreshed_approval_id=refreshed_approval_id,
        refreshed_digest=refreshed_digest,
    )
