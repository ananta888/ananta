"""Handling of Hub approval decisions for recovery plans.

Split out of ``task_recovery_planning_service`` (SRP): validating an approval
decision against the exact plan binding and either materializing and releasing
the approved recovery plan or rejecting it. ``TaskRecoveryPlanningService``
composes :class:`RecoveryApprovalDecision` with only the collaborators it
uses (lock bundle, task CAS port, approval saga, release step, Hub role,
repository/approval/planner/planning-service providers, policy binding and
audit sink) and keeps its public method as a thin delegator.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from agent.services.approval_auto_grant_policy import RECOVERY_MATERIALIZE_TOOL
from agent.services.recovery_plan_contract import calculate_recovery_materialization_inputs_digest
from agent.services.task_recovery_approval_saga import RecoveryApprovalSaga
from agent.services.task_recovery_planning_rules import (
    _TERMINAL_GOAL_STATUSES,
    _TERMINAL_TASK_STATUSES,
    RECOVERY_STATE_SCHEMA,
    is_terminal,
    plan_action_configured,
    plan_digest,
    reject_plan,
    stored_team_binding_matches,
    team_binding_matches,
)
from agent.services.task_recovery_planning_values import mapping as _mapping
from agent.services.task_recovery_planning_values import (
    transitioned_recovery_strategy as _transitioned_recovery_strategy,
)
from agent.services.task_recovery_ports import (
    AuditSink,
    ConditionalTaskUpdate,
    PolicyBinding,
    RecoveryLocks,
    ServiceProvider,
)
from agent.services.task_recovery_release import RecoveryRelease


class RecoveryApprovalDecision:
    """Apply one Hub approval decision to its exactly bound recovery plan."""

    def __init__(
        self,
        *,
        locks: RecoveryLocks,
        conditional_update: ConditionalTaskUpdate,
        saga: RecoveryApprovalSaga,
        release: RecoveryRelease,
        role: Callable[[], str],
        repositories: ServiceProvider,
        approval_service: ServiceProvider,
        planner: ServiceProvider,
        planning_service: ServiceProvider,
        policy_binding: PolicyBinding,
        audit: AuditSink,
    ) -> None:
        self._locks = locks
        self._conditional_update = conditional_update
        self._saga = saga
        self._release = release
        self._role = role
        self._repositories = repositories
        self._approval_service = approval_service
        self._planner = planner
        self._planning_service = planning_service
        self._policy_binding = policy_binding
        self._audit = audit

    def handle_approval_decision(self, approval: Any) -> dict[str, Any]:
        """Apply a recovery approval decision; no other approval tool is handled."""
        if str(self._role() or "").strip().lower() != "hub":
            return {"status": "ignored", "reason_code": "hub_role_required"}
        if str(getattr(approval, "tool_name", "") or "") != RECOVERY_MATERIALIZE_TOOL:
            return {"status": "ignored", "reason_code": "approval_tool_not_handled"}

        args = _mapping(getattr(approval, "canonical_arguments", None))
        plan_id = str(args.get("plan_id") or "").strip()
        goal_id = str(args.get("goal_id") or "").strip()
        source_task_id = str(args.get("source_task_id") or "").strip()
        recovery_key = str(args.get("recovery_key") or "").strip()
        expected_policy_hash = str(args.get("policy_hash") or "").strip()
        expected_team_id = str(args.get("team_id") or "").strip()
        if not all(
            (
                plan_id,
                goal_id,
                source_task_id,
                recovery_key,
                expected_policy_hash,
            )
        ) or "team_id" not in args:
            return {"status": "failed", "reason_code": "approval_binding_incomplete"}

        with (
            self._locks.lock_for(plan_id),
            self._locks.distributed_recovery_lock(recovery_key) as distributed_lock_acquired,
        ):
            if not distributed_lock_acquired:
                # Another Hub owns the same exact recovery mutation. Keep the
                # durable grant intact; reconciliation will observe its result.
                return {
                    "status": "ignored",
                    "reason_code": "recovery_action_in_progress",
                    "plan_id": plan_id,
                }
            approval_service = self._approval_service()
            get_request = getattr(approval_service, "get_request", None)
            if callable(get_request):
                authoritative = get_request(str(getattr(approval, "id", "") or ""))
                if authoritative is not None:
                    approval = authoritative
            repos = self._repositories()
            plan = repos.plan_repo.get_by_id(plan_id)
            nodes = repos.plan_node_repo.get_by_plan_id(plan_id)
            source_task = repos.task_repo.get_by_id(source_task_id)
            goal = repos.goal_repo.get_by_id(goal_id)
            if plan is None or source_task is None or goal is None or not nodes:
                return {"status": "failed", "reason_code": "approval_target_not_found"}
            rationale = _mapping(getattr(plan, "rationale", None))
            source_details = _mapping(getattr(source_task, "status_reason_details", None))
            source_recovery = _mapping(source_details.get("model_recovery"))
            approval_id = str(getattr(approval, "id", "") or "")
            decision = str(getattr(approval, "status", "") or "").strip().lower()
            team_bindings_match = (
                stored_team_binding_matches(
                    rationale,
                    expected_team_id,
                )
                and team_binding_matches(
                    source_task=source_task,
                    goal=goal,
                    expected_team_id=expected_team_id,
                )
            )
            if not team_bindings_match:
                reject_plan(
                    repos,
                    plan,
                    reason_code="recovery_team_binding_changed",
                )
                if decision == "granted":
                    consumed = approval_service.consume_request(
                        approval_id
                    )
                    if consumed is None:
                        return {
                            "status": "failed",
                            "reason_code": "approval_consume_failed",
                            "plan_id": plan_id,
                        }
                return {
                    "status": "stopped",
                    "reason_code": "recovery_team_binding_changed",
                    "plan_id": plan_id,
                    "approval_status": (
                        "consumed"
                        if decision in {"granted", "consumed"}
                        else decision
                    ),
                }
            refresh_saga = _mapping(
                rationale.get("approval_refresh")
            )
            stale_refresh_id = str(
                refresh_saga.get("stale_approval_request_id") or ""
            )
            refreshed_id = str(
                refresh_saga.get(
                    "refreshed_approval_request_id"
                )
                or ""
            )
            refresh_state = str(
                refresh_saga.get("state") or ""
            )
            if approval_id == stale_refresh_id:
                return self._saga.refresh_stale_plan_approval(
                    repos=repos,
                    plan_id=plan_id,
                    goal_id=goal_id,
                    source_task_id=source_task_id,
                    recovery_key=recovery_key,
                    policy_hash=expected_policy_hash,
                    team_id=expected_team_id,
                    stale_approval_id=approval_id,
                )
            if (
                approval_id == refreshed_id
                and refresh_state != "completed"
            ):
                refresh_result = (
                    self._saga.complete_approval_refresh_saga(
                        repos=repos,
                        plan_id=plan_id,
                        goal_id=goal_id,
                        source_task_id=source_task_id,
                        recovery_key=recovery_key,
                        team_id=expected_team_id,
                        node_count=len(nodes),
                        stale_approval_id=stale_refresh_id,
                        refreshed_approval_id=refreshed_id,
                        refreshed_digest=str(
                            refresh_saga.get(
                                "refreshed_plan_digest"
                            )
                            or ""
                        ),
                    )
                )
                if (
                    str(refresh_result.get("status") or "")
                    != "pending_approval"
                ):
                    return refresh_result
                plan = repos.plan_repo.get_by_id(plan_id)
                source_task = repos.task_repo.get_by_id(
                    source_task_id
                )
                if plan is None or source_task is None:
                    return {
                        "status": "failed",
                        "reason_code": "approval_target_not_found",
                    }
                rationale = _mapping(
                    getattr(plan, "rationale", None)
                )
                source_details = _mapping(
                    getattr(
                        source_task,
                        "status_reason_details",
                        None,
                    )
                )
                source_recovery = _mapping(
                    source_details.get("model_recovery")
                )
            plan_bindings_match = (
                str(getattr(plan, "goal_id", "") or "") == goal_id
                and str(getattr(source_task, "goal_id", "") or "") == goal_id
                and str(rationale.get("recovery_key") or "") == recovery_key
                and str(rationale.get("policy_hash") or "") == expected_policy_hash
                and str(rationale.get("source_task_id") or "") == source_task_id
                and str(rationale.get("approval_request_id") or "") == approval_id
                and stored_team_binding_matches(
                    rationale,
                    expected_team_id,
                )
            )
            source_binding_matches = (
                str(source_recovery.get("recovery_key") or "") == recovery_key
                and str(source_recovery.get("plan_id") or "") == plan_id
                and str(source_recovery.get("approval_request_id") or "") == approval_id
                and stored_team_binding_matches(
                    source_recovery,
                    expected_team_id,
                )
            )
            source_binding_empty = not any(
                str(source_recovery.get(key) or "").strip()
                for key in (
                    "recovery_key",
                    "plan_id",
                    "approval_request_id",
                )
            )
            if not plan_bindings_match or not (source_binding_matches or source_binding_empty):
                return {"status": "failed", "reason_code": "approval_binding_mismatch"}
            if source_binding_empty and decision in {"granted", "consumed"}:
                with (
                    self._locks.distributed_source_lock(source_task_id) as source_lock_acquired,
                    self._locks.source_mutation_lock(source_task_id),
                ):
                    source_task = repos.task_repo.get_by_id(source_task_id)
                    if (
                        not source_lock_acquired
                        or source_task is None
                        or not self._saga.mark_source_waiting_for_approval(
                            source_task=source_task,
                            plan_id=plan_id,
                            approval_request_id=approval_id,
                            recovery_key=recovery_key,
                            node_count=len(nodes),
                            team_id=expected_team_id,
                        )
                    ):
                        return {
                            "status": "failed",
                            "reason_code": ("recovery_source_transition_conflict"),
                        }
                source_task = repos.task_repo.get_by_id(source_task_id)
                if source_task is None:
                    return {
                        "status": "failed",
                        "reason_code": "approval_target_not_found",
                    }
            if decision == "consumed":
                if str(getattr(plan, "status", "") or "") != "materialized":
                    return {
                        "status": "failed",
                        "reason_code": ("consumed_approval_plan_not_materialized"),
                        "plan_id": plan_id,
                    }
                created_task_ids = [
                    str(getattr(node, "materialized_task_id", "") or "")
                    for node in nodes
                    if str(getattr(node, "materialized_task_id", "") or "").strip()
                ]
                if not created_task_ids:
                    return {
                        "status": "failed",
                        "reason_code": "materialized_tasks_missing",
                        "plan_id": plan_id,
                    }
                return self._release.release_materialized_recovery(
                    repos=repos,
                    plan=plan,
                    nodes=nodes,
                    source_task_id=source_task_id,
                    goal_id=goal_id,
                    approval_id=approval_id,
                    recovery_key=recovery_key,
                    team_id=expected_team_id,
                    created_task_ids=created_task_ids,
                )
            if decision == "denied":
                with self._locks.plan_mutation_lock(plan_id) as plan_lock_acquired:
                    if not plan_lock_acquired:
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
                    plan.status = "rejected"
                    plan.rationale = {
                        **_mapping(getattr(plan, "rationale", None)),
                        "approval_state": "denied",
                        "approval_request_id": approval_id,
                    }
                    plan.updated_at = time.time()
                    repos.plan_repo.save(plan)
                source_task = repos.task_repo.get_by_id(source_task_id)
                current_source_status = str(getattr(source_task, "status", "") or "").strip().lower()
                if source_task is not None and current_source_status not in _TERMINAL_TASK_STATUSES:
                    denied_recovery_state = {
                        "schema": RECOVERY_STATE_SCHEMA,
                        "status": "denied",
                        "plan_id": plan_id,
                        "approval_request_id": approval_id,
                        "recovery_key": recovery_key,
                        "recovery_depth": 1,
                    }
                    denied_strategy_state = (
                        _transitioned_recovery_strategy(
                            source_task,
                            status="denied",
                            reason_code="recovery_plan_denied",
                        )
                    )
                    source_transitioned = self._conditional_update(
                        source_task_id,
                        "needs_review",
                        expected_statuses={current_source_status},
                        force=False,
                        status_reason_code="recovery_plan_denied",
                        verification_status={
                            **_mapping(
                                getattr(
                                    source_task,
                                    "verification_status",
                                    None,
                                )
                            ),
                            "model_recovery": denied_recovery_state,
                            "model_recovery_strategy": (
                                denied_strategy_state
                            ),
                        },
                        status_reason_details={
                            **_mapping(
                                getattr(
                                    source_task,
                                    "status_reason_details",
                                    None,
                                )
                            ),
                            "model_recovery": denied_recovery_state,
                            "model_recovery_strategy": (
                                denied_strategy_state
                            ),
                        },
                        event_type="task_recovery_plan_denied",
                        event_actor="hub_approval_dispatcher",
                        event_details={"plan_id": plan_id},
                    )
                    if not source_transitioned:
                        confirmed_source = repos.task_repo.get_by_id(source_task_id)
                        confirmed_recovery = _mapping(
                            _mapping(
                                getattr(
                                    confirmed_source,
                                    "status_reason_details",
                                    None,
                                )
                            ).get("model_recovery")
                        )
                        denial_confirmed = bool(
                            confirmed_source is not None
                            and (
                                is_terminal(
                                    confirmed_source,
                                    _TERMINAL_TASK_STATUSES,
                                )
                                or (
                                    str(confirmed_recovery.get("status") or "") == "denied"
                                    and str(confirmed_recovery.get("approval_request_id") or "") == approval_id
                                )
                            )
                        )
                        if not denial_confirmed:
                            return {
                                "status": "failed",
                                "reason_code": ("recovery_source_transition_conflict"),
                                "plan_id": plan_id,
                            }
                self._audit(
                    "task_recovery_plan_denied",
                    {
                        "task_id": source_task_id,
                        "goal_id": goal_id,
                        "plan_id": plan_id,
                        "approval_request_id": approval_id,
                    },
                )
                return {
                    "status": "denied",
                    "reason_code": "recovery_plan_denied",
                    "plan_id": plan_id,
                }
            goal_terminal = is_terminal(
                goal,
                _TERMINAL_GOAL_STATUSES,
            )
            source_status = str(getattr(source_task, "status", "") or "").strip().lower()
            source_terminal = source_status in _TERMINAL_TASK_STATUSES
            source_state_changed = source_status not in {"waiting_for_review", "needs_review"}
            if goal_terminal or source_terminal or source_state_changed:
                reason_code = (
                    "recovery_goal_terminal"
                    if goal_terminal
                    else ("recovery_source_terminal" if source_terminal else "recovery_source_state_changed")
                )
                if decision == "granted":
                    reject_plan(
                        repos,
                        plan,
                        reason_code=reason_code,
                    )
                    consumed = approval_service.consume_request(approval_id)
                    if consumed is not None:
                        return {
                            "status": "stopped",
                            "reason_code": reason_code,
                            "plan_id": plan_id,
                            "approval_status": "consumed",
                        }
                return {
                    "status": "failed",
                    "reason_code": reason_code,
                }

            if decision != "granted":
                return {"status": "ignored", "reason_code": f"approval_not_granted:{decision}"}

            (
                current_actions,
                current_approval_required,
                current_policy_hash,
            ) = self._policy_binding(source_task)
            if (
                not plan_action_configured(current_actions)
                or "require_approval" not in current_actions
                or not current_approval_required
                or current_policy_hash != expected_policy_hash
            ):
                reject_plan(
                    repos,
                    plan,
                    reason_code="recovery_policy_changed",
                )
                consumed = approval_service.consume_request(approval_id)
                if consumed is None:
                    return {
                        "status": "failed",
                        "reason_code": "approval_consume_failed",
                        "plan_id": plan_id,
                    }
                return {
                    "status": "stopped",
                    "reason_code": "recovery_policy_changed",
                    "plan_id": plan_id,
                    "approval_status": "consumed",
                }

            current_digest = plan_digest(plan, nodes)
            expected_digest = str(args.get("plan_digest") or "")
            if (
                current_digest != expected_digest
                or str(getattr(approval, "target_fingerprint", "") or "") != current_digest
                or str(rationale.get("plan_digest") or "") != current_digest
            ):
                policy_hash = str(rationale.get("policy_hash") or "").strip()
                if not policy_hash:
                    return {
                        "status": "failed",
                        "reason_code": "recovery_policy_binding_missing",
                    }
                return self._saga.refresh_stale_plan_approval(
                    repos=repos,
                    plan_id=plan_id,
                    goal_id=goal_id,
                    source_task_id=source_task_id,
                    recovery_key=recovery_key,
                    policy_hash=policy_hash,
                    team_id=expected_team_id,
                    stale_approval_id=approval_id,
                )

            source_task = repos.task_repo.get_by_id(source_task_id)
            goal = repos.goal_repo.get_by_id(goal_id)
            if (
                source_task is None
                or goal is None
                or is_terminal(source_task, _TERMINAL_TASK_STATUSES)
                or is_terminal(goal, _TERMINAL_GOAL_STATUSES)
                or str(getattr(source_task, "status", "") or "").strip().lower()
                not in {"waiting_for_review", "needs_review"}
            ):
                reason_code = (
                    "recovery_goal_terminal"
                    if goal is None or is_terminal(goal, _TERMINAL_GOAL_STATUSES)
                    else (
                        "recovery_source_terminal"
                        if source_task is None
                        or is_terminal(
                            source_task,
                            _TERMINAL_TASK_STATUSES,
                        )
                        else "recovery_source_state_changed"
                    )
                )
                reject_plan(
                    repos,
                    plan,
                    reason_code=reason_code,
                )
                consumed = approval_service.consume_request(approval_id)
                if consumed is None:
                    return {
                        "status": "failed",
                        "reason_code": "approval_consume_failed",
                        "plan_id": plan_id,
                    }
                return {
                    "status": "stopped",
                    "reason_code": reason_code,
                    "plan_id": plan_id,
                    "approval_status": "consumed",
                }
            approved_materialization_inputs_digest = str(
                rationale.get("materialization_inputs_digest")
                or ""
            )
            current_materialization_inputs_digest = (
                calculate_recovery_materialization_inputs_digest(
                    goal
                )
            )
            if (
                not approved_materialization_inputs_digest
                or approved_materialization_inputs_digest
                != current_materialization_inputs_digest
            ):
                reject_plan(
                    repos,
                    plan,
                    reason_code=(
                        "recovery_materialization_inputs_changed"
                    ),
                )
                consumed = approval_service.consume_request(
                    approval_id
                )
                if consumed is None:
                    return {
                        "status": "failed",
                        "reason_code": "approval_consume_failed",
                        "plan_id": plan_id,
                    }
                return {
                    "status": "stopped",
                    "reason_code": (
                        "recovery_materialization_inputs_changed"
                    ),
                    "plan_id": plan_id,
                    "approval_status": "consumed",
                }

            result = self._planning_service().materialize_existing_plan(
                planner=self._planner(),
                plan_id=plan_id,
                approval_request_id=str(getattr(approval, "id", "") or ""),
                team_id=expected_team_id or None,
                parent_task_id=None,
                source_task_id=source_task_id,
                expected_plan_digest=expected_digest,
                # Dependency reconciliation automatically releases root nodes
                # which have no ``depends_on`` entries.  ``paused`` is the
                # canonical inert state that remains non-dispatchable until
                # the exact approval has been consumed below.
                initial_task_status="paused",
            )
            if str(result.get("reason_code") or "") == "recovery_plan_digest_stale":
                return self._saga.refresh_stale_plan_approval(
                    repos=repos,
                    plan_id=plan_id,
                    goal_id=goal_id,
                    source_task_id=source_task_id,
                    recovery_key=recovery_key,
                    policy_hash=expected_policy_hash,
                    team_id=expected_team_id,
                    stale_approval_id=approval_id,
                )
            if str(result.get("status") or "") != "materialized":
                return dict(result)

            plan = repos.plan_repo.get_by_id(plan_id) or plan
            nodes = repos.plan_node_repo.get_by_plan_id(plan_id) or nodes
            plan.status = "materialized"
            created_task_ids = [
                str(value) for value in list(result.get("created_task_ids") or []) if str(value).strip()
            ]
            consumed = approval_service.consume_request(approval_id)
            if consumed is None:
                return {
                    "status": "failed",
                    "reason_code": "approval_consume_failed",
                    "plan_id": plan_id,
                    "created_task_ids": created_task_ids,
                }

            release_result = self._release.release_materialized_recovery(
                repos=repos,
                plan=plan,
                nodes=nodes,
                source_task_id=source_task_id,
                goal_id=goal_id,
                approval_id=approval_id,
                recovery_key=recovery_key,
                team_id=expected_team_id,
                created_task_ids=created_task_ids,
            )
            self._audit(
                "task_recovery_plan_materialized",
                {
                    "task_id": source_task_id,
                    "goal_id": goal_id,
                    "plan_id": plan_id,
                    "approval_request_id": str(getattr(approval, "id", "") or ""),
                    "created_task_ids": created_task_ids,
                },
            )
            return {
                **dict(result),
                **release_result,
            }
