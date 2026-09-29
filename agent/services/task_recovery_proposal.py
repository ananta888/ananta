"""Recovery-plan proposal after bounded model fallback exhaustion.

Split out of ``task_recovery_planning_service`` (SRP): turning a worker's
``model_recovery_signal.v1`` into a persisted draft recovery plan, or resuming
an existing plan through its saga. :class:`RecoveryProposal` receives only
the collaborators it uses (lock bundle, approval saga, Hub role, repository,
planner and approval-service providers, policy binding, audit sink);
``TaskRecoveryPlanningService`` composes it and keeps its historic method
names as thin delegators.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from agent.services.task_recovery_approval_saga import RecoveryApprovalSaga
from agent.services.task_recovery_planning_rules import (
    _TERMINAL_GOAL_STATUSES,
    _TERMINAL_TASK_STATUSES,
    RECOVERY_STATE_SCHEMA,
    _record_recovery_cut,
    active_plan_for_source,
    existing_plan,
    is_terminal,
    plan_action_configured,
    reject_plan,
    stored_team_binding_matches,
    task_recovery_depth,
    team_binding_matches,
)
from agent.services.task_recovery_planning_values import mapping as _mapping
from agent.services.task_recovery_planning_values import sha256_json as _sha256_json
from agent.services.task_recovery_ports import (
    AuditSink,
    PolicyBinding,
    RecoveryLocks,
    ServiceProvider,
)
from agent.services.task_recovery_signal_context import (
    compact_recovery_context,
    summarize_exhaustion_signal,
)


class RecoveryProposal:
    """Persist one approval-gated recovery draft, or resume its interrupted saga."""

    def __init__(
        self,
        *,
        locks: RecoveryLocks,
        saga: RecoveryApprovalSaga,
        role: Callable[[], str],
        repositories: ServiceProvider,
        planner: ServiceProvider,
        approval_service: ServiceProvider,
        policy_binding: PolicyBinding,
        audit: AuditSink,
    ) -> None:
        self._locks = locks
        self._saga = saga
        self._role = role
        self._repositories = repositories
        self._planner = planner
        self._approval_service = approval_service
        self._policy_binding = policy_binding
        self._audit = audit

    def resume_existing_plan_saga(
        self,
        *,
        repos: Any,
        plan: Any,
        source_task: Any,
        goal: Any,
        goal_id: str,
        source_task_id: str,
        recovery_key: str,
        policy_hash: str,
        team_id: str,
    ) -> dict[str, Any]:
        """Deterministically finish an interrupted plan/approval/source saga."""

        rationale = _mapping(getattr(plan, "rationale", None))
        plan_id = str(getattr(plan, "id", "") or "")
        plan_status = str(getattr(plan, "status", "") or "").strip().lower()
        if plan_status not in {
            "draft",
            "pending_approval",
            "approved",
        }:
            return {
                "status": plan_status or "failed",
                "reason_code": "recovery_plan_already_exists",
                "plan_id": plan_id,
                "approval_request_id": rationale.get("approval_request_id"),
                "recovery_key": recovery_key,
            }
        if (
            str(rationale.get("source_task_id") or source_task_id) != source_task_id
            or str(rationale.get("policy_hash") or policy_hash) != policy_hash
            or not stored_team_binding_matches(
                rationale,
                team_id,
            )
            or not team_binding_matches(
                source_task=source_task,
                goal=goal,
                expected_team_id=team_id,
            )
        ):
            return {
                "status": "failed",
                "reason_code": "recovery_plan_binding_conflict",
                "plan_id": plan_id,
            }
        approval_request_id = str(rationale.get("approval_request_id") or "").strip()
        source_recovery = _mapping(_mapping(getattr(source_task, "status_reason_details", None)).get("model_recovery"))
        source_binding_complete = bool(
            approval_request_id
            and str(source_recovery.get("plan_id") or "") == plan_id
            and str(source_recovery.get("approval_request_id") or "") == approval_request_id
            and str(source_recovery.get("recovery_key") or "") == recovery_key
        )
        if source_binding_complete:
            get_request = getattr(
                self._approval_service(),
                "get_request",
                None,
            )
            approval_exists = not callable(get_request) or get_request(approval_request_id) is not None
            if approval_exists:
                return {
                    "status": plan_status,
                    "reason_code": "recovery_plan_already_exists",
                    "plan_id": plan_id,
                    "approval_request_id": approval_request_id,
                    "recovery_key": recovery_key,
                }
        nodes = repos.plan_node_repo.get_by_plan_id(plan_id)
        if not nodes:
            reject_plan(
                repos,
                plan,
                reason_code="recovery_plan_nodes_missing",
            )
            return {
                "status": "failed",
                "reason_code": "recovery_plan_nodes_missing",
                "plan_id": plan_id,
            }
        expected_node_count = max(
            0,
            int(rationale.get("node_count") or 0),
        )
        if expected_node_count and len(nodes) != expected_node_count:
            reject_plan(
                repos,
                plan,
                reason_code="recovery_plan_nodes_incomplete",
            )
            return {
                "status": "failed",
                "reason_code": "recovery_plan_nodes_incomplete",
                "plan_id": plan_id,
            }
        if is_terminal(source_task, _TERMINAL_TASK_STATUSES) or is_terminal(goal, _TERMINAL_GOAL_STATUSES):
            reason_code = (
                "recovery_source_terminal"
                if is_terminal(
                    source_task,
                    _TERMINAL_TASK_STATUSES,
                )
                else "recovery_goal_terminal"
            )
            reject_plan(repos, plan, reason_code=reason_code)
            return {"status": "stopped", "reason_code": reason_code}

        with self._locks.plan_mutation_lock(plan_id) as plan_lock_acquired:
            if not plan_lock_acquired:
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
                    "reason_code": "recovery_plan_persistence_failed",
                }
            plan.status = "pending_approval"
            plan.planning_mode = "task_recovery"
            plan.rationale = {
                **_mapping(getattr(plan, "rationale", None)),
                "recovery_schema": RECOVERY_STATE_SCHEMA,
                "recovery_key": recovery_key,
                "source_task_id": source_task_id,
                "recovery_depth": 1,
                "policy_hash": policy_hash,
                "team_id": team_id,
                "approval_state": "pending",
            }
            plan.updated_at = time.time()
            plan = repos.plan_repo.save(plan)
            approval, plan_digest = self._saga.request_materialization_approval(
                repos=repos,
                plan=plan,
                nodes=nodes,
                goal_id=goal_id,
                source_task_id=source_task_id,
                recovery_key=recovery_key,
                policy_hash=policy_hash,
                team_id=team_id,
            )

        source_task = repos.task_repo.get_by_id(source_task_id)
        if source_task is None or not self._saga.mark_source_waiting_for_approval(
            source_task=source_task,
            plan_id=plan_id,
            approval_request_id=str(approval.id),
            recovery_key=recovery_key,
            node_count=len(nodes),
            team_id=team_id,
        ):
            return {
                "status": "failed",
                "reason_code": "recovery_source_transition_conflict",
                "plan_id": plan_id,
                "approval_request_id": str(approval.id),
            }
        return {
            "status": "pending_approval",
            "reason_code": "recovery_plan_saga_resumed",
            "plan_id": plan_id,
            "approval_request_id": str(approval.id),
            "plan_digest": plan_digest,
            "recovery_key": recovery_key,
            "node_count": len(nodes),
        }


    def propose_after_model_exhaustion(
        self,
        *,
        task: Any,
        strategy_failures: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Persist one approval-gated draft for an eligible exhausted task."""
        if str(self._role() or "").strip().lower() != "hub":
            return {"status": "ignored", "reason_code": "hub_role_required"}

        task_data = _mapping(task)
        task_id = str(task_data.get("id") or getattr(task, "id", "") or "").strip()
        goal_id = str(task_data.get("goal_id") or getattr(task, "goal_id", "") or "").strip()
        if not task_id or not goal_id:
            return {"status": "ignored", "reason_code": "task_goal_binding_required"}
        recovery_depth = task_recovery_depth(task)

        actions, approval_required, policy_hash = self._policy_binding(task)
        if not plan_action_configured(actions):
            return {"status": "ignored", "reason_code": "recovery_plan_not_configured"}
        if "require_approval" not in actions or not approval_required:
            return {"status": "stopped", "reason_code": "recovery_plan_approval_required"}

        signal = summarize_exhaustion_signal(strategy_failures)
        if signal is None:
            return {"status": "ignored", "reason_code": "model_exhaustion_signal_required"}

        repos = self._repositories()
        source_task = repos.task_repo.get_by_id(task_id)
        goal = repos.goal_repo.get_by_id(goal_id)
        if source_task is None or goal is None:
            return {"status": "ignored", "reason_code": "authoritative_binding_not_found"}
        if str(getattr(source_task, "goal_id", "") or "") != goal_id:
            return {"status": "stopped", "reason_code": "task_goal_binding_mismatch"}
        team_id = str(getattr(goal, "team_id", "") or "").strip()
        if not team_binding_matches(
            source_task=source_task,
            goal=goal,
            expected_team_id=team_id,
        ):
            return {
                "status": "stopped",
                "reason_code": "recovery_team_binding_mismatch",
            }
        if str(getattr(goal, "status", "") or "").strip().lower() in _TERMINAL_GOAL_STATUSES:
            return {"status": "stopped", "reason_code": "recovery_goal_terminal"}
        if str(getattr(source_task, "status", "") or "").strip().lower() in _TERMINAL_TASK_STATUSES:
            return {"status": "stopped", "reason_code": "recovery_source_terminal"}

        failure_fingerprint = _sha256_json(
            {
                "task_id": task_id,
                "signal": signal,
            }
        )
        recovery_key = _sha256_json(
            {
                "task_id": task_id,
                "team_id": team_id,
                "policy_hash": policy_hash,
                "failure_fingerprint": failure_fingerprint,
            }
        )

        with (
            self._locks.lock_for(recovery_key),
            self._locks.distributed_recovery_lock(recovery_key) as lock_acquired,
        ):
            if not lock_acquired:
                return {
                    "status": "ignored",
                    "reason_code": "recovery_plan_generation_in_progress",
                    "recovery_key": recovery_key,
                }
            existing = existing_plan(
                repos,
                goal_id=goal_id,
                recovery_key=recovery_key,
            )
            if existing is not None:
                with self._locks.distributed_source_lock(task_id) as source_lock_acquired:
                    if not source_lock_acquired:
                        return {
                            "status": "ignored",
                            "reason_code": ("recovery_plan_generation_in_progress"),
                            "recovery_key": recovery_key,
                        }
                    with self._locks.source_mutation_lock(task_id):
                        authoritative_source = repos.task_repo.get_by_id(task_id)
                        authoritative_goal = repos.goal_repo.get_by_id(goal_id)
                        if authoritative_source is None or authoritative_goal is None:
                            return {
                                "status": "stopped",
                                "reason_code": ("authoritative_binding_not_found"),
                            }
                        return self.resume_existing_plan_saga(
                            repos=repos,
                            plan=existing,
                            source_task=authoritative_source,
                            goal=authoritative_goal,
                            goal_id=goal_id,
                            source_task_id=task_id,
                            recovery_key=recovery_key,
                            policy_hash=policy_hash,
                            team_id=team_id,
                        )
            if recovery_depth >= 1:
                return {
                    "status": "stopped",
                    "reason_code": "task_recovery_recursion_guard",
                }

            compacted_context, compaction_meta = compact_recovery_context(
                source_task,
                actions=actions,
            )
            title = str(getattr(source_task, "title", "") or "delegated task").strip()
            description = str(getattr(source_task, "description", "") or "").strip()
            recovery_goal = (
                f'Implement and validate a bounded recovery plan for task "{title[:240]}". '
                f"Original task: {_record_recovery_cut('recovery.goal', description, 4000)[:4000]}"
            )
            result = self._planner().plan_goal(
                goal=recovery_goal,
                context=compacted_context,
                team_id=team_id or None,
                parent_task_id=task_id,
                create_tasks=False,
                use_template=True,
                use_repo_context=False,
                goal_id=goal_id,
                goal_trace_id=str(getattr(source_task, "goal_trace_id", "") or getattr(goal, "trace_id", "") or ""),
                mode="generic",
                mode_data={
                    "task_recovery": True,
                    "source_task_id": task_id,
                    "segment_planning": "segment_planning" in actions,
                    "recovery_depth": 1,
                },
                initial_plan_rationale={
                    "recovery_schema": RECOVERY_STATE_SCHEMA,
                    "recovery_key": recovery_key,
                    "source_task_id": task_id,
                    "team_id": team_id,
                    "recovery_depth": 1,
                    "policy_hash": policy_hash,
                    "failure_fingerprint": failure_fingerprint,
                    "failure_signal": signal,
                    "recovery_actions": actions,
                    "compaction": compaction_meta,
                    "approval_state": "initializing",
                },
            )
            plan_id = str((result or {}).get("plan_id") or "").strip()
            if not plan_id or (result or {}).get("error"):
                reason_code = str(
                    (result or {}).get("error_classification")
                    or (result or {}).get("error")
                    or "recovery_plan_generation_failed"
                )[:160]
                self._audit(
                    "task_recovery_plan_failed",
                    {
                        "task_id": task_id,
                        "goal_id": goal_id,
                        "reason_code": reason_code,
                        "policy_hash": policy_hash,
                        "failure_fingerprint": failure_fingerprint,
                    },
                )
                return {"status": "failed", "reason_code": reason_code}

            plan = repos.plan_repo.get_by_id(plan_id)
            nodes = repos.plan_node_repo.get_by_plan_id(plan_id)
            if plan is None or not nodes or str(getattr(plan, "goal_id", "") or "") != goal_id:
                return {"status": "failed", "reason_code": "recovery_plan_persistence_failed"}

            # Planning may involve a slow LLM call. Re-read every authoritative
            # precondition before creating an approval or mutating the source.
            source_task = repos.task_repo.get_by_id(task_id)
            goal = repos.goal_repo.get_by_id(goal_id)
            if source_task is None or goal is None:
                reject_plan(
                    repos,
                    plan,
                    reason_code="authoritative_binding_not_found",
                )
                return {
                    "status": "stopped",
                    "reason_code": "authoritative_binding_not_found",
                }
            if (
                str(getattr(source_task, "goal_id", "") or "") != goal_id
                or not team_binding_matches(
                    source_task=source_task,
                    goal=goal,
                    expected_team_id=team_id,
                )
                or is_terminal(goal, _TERMINAL_GOAL_STATUSES)
                or is_terminal(source_task, _TERMINAL_TASK_STATUSES)
            ):
                reason_code = (
                    "recovery_goal_terminal"
                    if is_terminal(goal, _TERMINAL_GOAL_STATUSES)
                    else "recovery_source_terminal"
                )
                reject_plan(repos, plan, reason_code=reason_code)
                return {"status": "stopped", "reason_code": reason_code}
            (
                current_actions,
                current_approval_required,
                current_policy_hash,
            ) = self._policy_binding(source_task)
            if (
                not plan_action_configured(current_actions)
                or "require_approval" not in current_actions
                or not current_approval_required
                or current_policy_hash != policy_hash
            ):
                reject_plan(
                    repos,
                    plan,
                    reason_code="recovery_policy_changed",
                )
                return {
                    "status": "stopped",
                    "reason_code": "recovery_policy_changed",
                }

            with (
                self._locks.distributed_source_lock(task_id) as source_lock_acquired,
                self._locks.source_mutation_lock(task_id),
            ):
                if not source_lock_acquired:
                    return {
                        "status": "ignored",
                        "reason_code": ("recovery_plan_generation_in_progress"),
                        "recovery_key": recovery_key,
                    }
                source_task = repos.task_repo.get_by_id(task_id)
                goal = repos.goal_repo.get_by_id(goal_id)
                if (
                    source_task is None
                    or goal is None
                    or is_terminal(
                        source_task,
                        _TERMINAL_TASK_STATUSES,
                    )
                    or is_terminal(
                        goal,
                        _TERMINAL_GOAL_STATUSES,
                    )
                    or not team_binding_matches(
                        source_task=source_task,
                        goal=goal,
                        expected_team_id=team_id,
                    )
                ):
                    reason_code = (
                        "recovery_goal_terminal"
                        if goal is None
                        or is_terminal(
                            goal,
                            _TERMINAL_GOAL_STATUSES,
                        )
                        else "recovery_source_terminal"
                    )
                    reject_plan(
                        repos,
                        plan,
                        reason_code=reason_code,
                    )
                    return {
                        "status": "stopped",
                        "reason_code": reason_code,
                    }
                active_plan = active_plan_for_source(
                    repos,
                    goal_id=goal_id,
                    source_task_id=task_id,
                    exclude_plan_id=plan_id,
                )
                authoritative_depth = task_recovery_depth(source_task)
                if active_plan is not None:
                    reject_plan(
                        repos,
                        plan,
                        reason_code=("recovery_source_plan_already_active"),
                    )
                    active_rationale = _mapping(getattr(active_plan, "rationale", None))
                    return {
                        "status": str(getattr(active_plan, "status", "") or "draft"),
                        "reason_code": ("recovery_plan_already_exists_for_source"),
                        "plan_id": str(getattr(active_plan, "id", "") or ""),
                        "approval_request_id": active_rationale.get("approval_request_id"),
                    }
                if authoritative_depth >= 1:
                    reject_plan(
                        repos,
                        plan,
                        reason_code="task_recovery_recursion_guard",
                    )
                    return {
                        "status": "stopped",
                        "reason_code": ("task_recovery_recursion_guard"),
                    }
                (
                    locked_actions,
                    locked_approval_required,
                    locked_policy_hash,
                ) = self._policy_binding(source_task)
                if (
                    not plan_action_configured(locked_actions)
                    or "require_approval" not in locked_actions
                    or not locked_approval_required
                    or locked_policy_hash != policy_hash
                ):
                    reject_plan(
                        repos,
                        plan,
                        reason_code="recovery_policy_changed",
                    )
                    return {
                        "status": "stopped",
                        "reason_code": "recovery_policy_changed",
                    }

                with self._locks.plan_mutation_lock(plan_id) as plan_lock_acquired:
                    if not plan_lock_acquired:
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
                            "reason_code": ("recovery_plan_persistence_failed"),
                        }
                    plan.status = "pending_approval"
                    plan.planning_mode = "task_recovery"
                    plan.rationale = {
                        **_mapping(getattr(plan, "rationale", None)),
                        "recovery_schema": RECOVERY_STATE_SCHEMA,
                        "recovery_key": recovery_key,
                        "source_task_id": task_id,
                        "team_id": team_id,
                        "recovery_depth": 1,
                        "policy_hash": policy_hash,
                        "failure_fingerprint": failure_fingerprint,
                        "failure_signal": signal,
                        "recovery_actions": actions,
                        "compaction": compaction_meta,
                        "approval_state": "pending",
                    }
                    plan.updated_at = time.time()
                    plan = repos.plan_repo.save(plan)
                    approval, plan_digest = self._saga.request_materialization_approval(
                        repos=repos,
                        plan=plan,
                        nodes=nodes,
                        goal_id=goal_id,
                        source_task_id=task_id,
                        recovery_key=recovery_key,
                        policy_hash=policy_hash,
                        team_id=team_id,
                    )
                source_transitioned = self._saga.mark_source_waiting_for_approval(
                    source_task=source_task,
                    plan_id=plan_id,
                    approval_request_id=str(approval.id),
                    recovery_key=recovery_key,
                    node_count=len(nodes),
                    team_id=team_id,
                )
                if not source_transitioned:
                    return {
                        "status": "failed",
                        "reason_code": ("recovery_source_transition_conflict"),
                        "plan_id": plan_id,
                        "approval_request_id": str(approval.id),
                    }
                self._audit(
                    "task_recovery_plan_proposed",
                    {
                        "task_id": task_id,
                        "goal_id": goal_id,
                        "plan_id": plan_id,
                        "approval_request_id": approval.id,
                        "policy_hash": policy_hash,
                        "failure_fingerprint": failure_fingerprint,
                        "node_count": len(nodes),
                    },
                )
                return {
                    "status": "pending_approval",
                    "reason_code": "recovery_plan_pending_approval",
                    "plan_id": plan_id,
                    "approval_request_id": approval.id,
                    "plan_digest": plan_digest,
                    "recovery_key": recovery_key,
                    "node_count": len(nodes),
                    "compaction": compaction_meta,
                }
