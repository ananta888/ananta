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
from dataclasses import dataclass, field
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
        binding = _ApprovalBinding.from_arguments(args)
        if binding is None:
            return {"status": "failed", "reason_code": "approval_binding_incomplete"}

        with (
            self._locks.lock_for(binding.plan_id),
            self._locks.distributed_recovery_lock(binding.recovery_key) as distributed_lock_acquired,
        ):
            if not distributed_lock_acquired:
                # Another Hub owns the same exact recovery mutation. Keep the
                # durable grant intact; reconciliation will observe its result.
                return {
                    "status": "ignored",
                    "reason_code": "recovery_action_in_progress",
                    "plan_id": binding.plan_id,
                }
            return self._apply_locked_decision(approval, binding=binding, args=args)

    def _apply_locked_decision(self, approval: Any, *, binding: _ApprovalBinding, args: dict) -> dict[str, Any]:
        """Run the ordered decision stages; the first stage with an outcome ends the decision."""
        ctx = self._load_decision_context(approval, binding=binding, args=args)
        if isinstance(ctx, dict):
            return ctx
        outcome = _first_outcome(
            ctx,
            self._stop_on_team_binding_change,
            self._route_approval_refresh,
            self._verify_plan_and_source_bindings,
        )
        if outcome is not None:
            return outcome
        if ctx.decision == "consumed":
            return self._release_consumed_approval(ctx)
        if ctx.decision == "denied":
            return self._deny_recovery_plan(ctx)
        outcome = self._stop_on_stale_recovery_state(ctx)
        if outcome is not None:
            return outcome
        if ctx.decision != "granted":
            return {"status": "ignored", "reason_code": f"approval_not_granted:{ctx.decision}"}
        outcome = _first_outcome(
            ctx,
            self._stop_on_policy_change,
            self._refresh_on_plan_digest_drift,
            self._stop_on_reloaded_recovery_state,
            self._stop_on_materialization_inputs_change,
        )
        if outcome is not None:
            return outcome
        return self._materialize_and_release(ctx)

    def _load_decision_context(
        self, approval: Any, *, binding: _ApprovalBinding, args: dict
    ) -> _DecisionContext | dict[str, Any]:
        approval_service = self._approval_service()
        get_request = getattr(approval_service, "get_request", None)
        if callable(get_request):
            authoritative = get_request(str(getattr(approval, "id", "") or ""))
            if authoritative is not None:
                approval = authoritative
        repos = self._repositories()
        plan = repos.plan_repo.get_by_id(binding.plan_id)
        nodes = repos.plan_node_repo.get_by_plan_id(binding.plan_id)
        source_task = repos.task_repo.get_by_id(binding.source_task_id)
        goal = repos.goal_repo.get_by_id(binding.goal_id)
        if plan is None or source_task is None or goal is None or not nodes:
            return {"status": "failed", "reason_code": "approval_target_not_found"}
        ctx = _DecisionContext(
            binding=binding,
            args=args,
            approval=approval,
            approval_id=str(getattr(approval, "id", "") or ""),
            decision=str(getattr(approval, "status", "") or "").strip().lower(),
            approval_service=approval_service,
            repos=repos,
            plan=plan,
            nodes=nodes,
            source_task=source_task,
            goal=goal,
        )
        ctx.refresh_plan_views()
        return ctx

    def _consume_or_fail(self, ctx: _DecisionContext, **failure_extra: Any) -> dict[str, Any] | None:
        """Consume the exact grant; return a failure outcome when consumption did not happen."""
        consumed = ctx.approval_service.consume_request(ctx.approval_id)
        if consumed is None:
            return {
                "status": "failed",
                "reason_code": "approval_consume_failed",
                "plan_id": ctx.binding.plan_id,
                **failure_extra,
            }
        return None

    def _reject_and_consume(self, ctx: _DecisionContext, reason_code: str) -> dict[str, Any]:
        reject_plan(ctx.repos, ctx.plan, reason_code=reason_code)
        failure = self._consume_or_fail(ctx)
        if failure is not None:
            return failure
        return {
            "status": "stopped",
            "reason_code": reason_code,
            "plan_id": ctx.binding.plan_id,
            "approval_status": "consumed",
        }

    def _stop_on_team_binding_change(self, ctx: _DecisionContext) -> dict[str, Any] | None:
        binding = ctx.binding
        team_bindings_match = stored_team_binding_matches(ctx.rationale, binding.team_id) and team_binding_matches(
            source_task=ctx.source_task,
            goal=ctx.goal,
            expected_team_id=binding.team_id,
        )
        if team_bindings_match:
            return None
        reject_plan(ctx.repos, ctx.plan, reason_code="recovery_team_binding_changed")
        if ctx.decision == "granted":
            failure = self._consume_or_fail(ctx)
            if failure is not None:
                return failure
        return {
            "status": "stopped",
            "reason_code": "recovery_team_binding_changed",
            "plan_id": binding.plan_id,
            "approval_status": ("consumed" if ctx.decision in {"granted", "consumed"} else ctx.decision),
        }

    def _route_approval_refresh(self, ctx: _DecisionContext) -> dict[str, Any] | None:
        """Continue an approval-refresh saga bound to this approval, if any."""
        binding = ctx.binding
        refresh_saga = _mapping(ctx.rationale.get("approval_refresh"))
        stale_refresh_id = str(refresh_saga.get("stale_approval_request_id") or "")
        refreshed_id = str(refresh_saga.get("refreshed_approval_request_id") or "")
        refresh_state = str(refresh_saga.get("state") or "")
        if ctx.approval_id == stale_refresh_id:
            return self._saga.refresh_stale_plan_approval(
                repos=ctx.repos,
                plan_id=binding.plan_id,
                goal_id=binding.goal_id,
                source_task_id=binding.source_task_id,
                recovery_key=binding.recovery_key,
                policy_hash=binding.policy_hash,
                team_id=binding.team_id,
                stale_approval_id=ctx.approval_id,
            )
        if ctx.approval_id != refreshed_id or refresh_state == "completed":
            return None
        refresh_result = self._saga.complete_approval_refresh_saga(
            repos=ctx.repos,
            plan_id=binding.plan_id,
            goal_id=binding.goal_id,
            source_task_id=binding.source_task_id,
            recovery_key=binding.recovery_key,
            team_id=binding.team_id,
            node_count=len(ctx.nodes),
            stale_approval_id=stale_refresh_id,
            refreshed_approval_id=refreshed_id,
            refreshed_digest=str(refresh_saga.get("refreshed_plan_digest") or ""),
        )
        if str(refresh_result.get("status") or "") != "pending_approval":
            return refresh_result
        plan = ctx.repos.plan_repo.get_by_id(binding.plan_id)
        source_task = ctx.repos.task_repo.get_by_id(binding.source_task_id)
        if plan is None or source_task is None:
            return {"status": "failed", "reason_code": "approval_target_not_found"}
        ctx.plan = plan
        ctx.source_task = source_task
        ctx.refresh_plan_views()
        return None

    def _verify_plan_and_source_bindings(self, ctx: _DecisionContext) -> dict[str, Any] | None:
        binding = ctx.binding
        source_binding_empty = not any(
            str(ctx.source_recovery.get(key) or "").strip()
            for key in (
                "recovery_key",
                "plan_id",
                "approval_request_id",
            )
        )
        if not _plan_binding_matches(ctx) or not (_source_binding_matches(ctx) or source_binding_empty):
            return {"status": "failed", "reason_code": "approval_binding_mismatch"}
        if not (source_binding_empty and ctx.decision in {"granted", "consumed"}):
            return None
        with (
            self._locks.distributed_source_lock(binding.source_task_id) as source_lock_acquired,
            self._locks.source_mutation_lock(binding.source_task_id),
        ):
            source_task = ctx.repos.task_repo.get_by_id(binding.source_task_id)
            if (
                not source_lock_acquired
                or source_task is None
                or not self._saga.mark_source_waiting_for_approval(
                    source_task=source_task,
                    plan_id=binding.plan_id,
                    approval_request_id=ctx.approval_id,
                    recovery_key=binding.recovery_key,
                    node_count=len(ctx.nodes),
                    team_id=binding.team_id,
                )
            ):
                return {
                    "status": "failed",
                    "reason_code": ("recovery_source_transition_conflict"),
                }
        ctx.source_task = ctx.repos.task_repo.get_by_id(binding.source_task_id)
        if ctx.source_task is None:
            return {
                "status": "failed",
                "reason_code": "approval_target_not_found",
            }
        return None

    def _release_consumed_approval(self, ctx: _DecisionContext) -> dict[str, Any]:
        binding = ctx.binding
        if str(getattr(ctx.plan, "status", "") or "") != "materialized":
            return {
                "status": "failed",
                "reason_code": ("consumed_approval_plan_not_materialized"),
                "plan_id": binding.plan_id,
            }
        created_task_ids = [
            str(getattr(node, "materialized_task_id", "") or "")
            for node in ctx.nodes
            if str(getattr(node, "materialized_task_id", "") or "").strip()
        ]
        if not created_task_ids:
            return {
                "status": "failed",
                "reason_code": "materialized_tasks_missing",
                "plan_id": binding.plan_id,
            }
        return self._release_materialized(ctx, created_task_ids)

    def _release_materialized(self, ctx: _DecisionContext, created_task_ids: list[str]) -> dict[str, Any]:
        binding = ctx.binding
        return self._release.release_materialized_recovery(
            repos=ctx.repos,
            plan=ctx.plan,
            nodes=ctx.nodes,
            source_task_id=binding.source_task_id,
            goal_id=binding.goal_id,
            approval_id=ctx.approval_id,
            recovery_key=binding.recovery_key,
            team_id=binding.team_id,
            created_task_ids=created_task_ids,
        )

    def _deny_recovery_plan(self, ctx: _DecisionContext) -> dict[str, Any]:
        binding = ctx.binding
        with self._locks.plan_mutation_lock(binding.plan_id) as plan_lock_acquired:
            if not plan_lock_acquired:
                return {
                    "status": "ignored",
                    "reason_code": "plan_mutation_in_progress",
                    "plan_id": binding.plan_id,
                }
            plan = ctx.repos.plan_repo.get_by_id(binding.plan_id)
            if plan is None:
                return {
                    "status": "failed",
                    "reason_code": "approval_target_not_found",
                }
            plan.status = "rejected"
            plan.rationale = {
                **_mapping(getattr(plan, "rationale", None)),
                "approval_state": "denied",
                "approval_request_id": ctx.approval_id,
            }
            plan.updated_at = time.time()
            ctx.repos.plan_repo.save(plan)
        conflict = self._transition_source_to_denied(ctx)
        if conflict is not None:
            return conflict
        self._audit(
            "task_recovery_plan_denied",
            {
                "task_id": binding.source_task_id,
                "goal_id": binding.goal_id,
                "plan_id": binding.plan_id,
                "approval_request_id": ctx.approval_id,
            },
        )
        return {
            "status": "denied",
            "reason_code": "recovery_plan_denied",
            "plan_id": binding.plan_id,
        }

    def _transition_source_to_denied(self, ctx: _DecisionContext) -> dict[str, Any] | None:
        binding = ctx.binding
        source_task = ctx.repos.task_repo.get_by_id(binding.source_task_id)
        current_source_status = str(getattr(source_task, "status", "") or "").strip().lower()
        if source_task is None or current_source_status in _TERMINAL_TASK_STATUSES:
            return None
        denied_recovery_state = {
            "schema": RECOVERY_STATE_SCHEMA,
            "status": "denied",
            "plan_id": binding.plan_id,
            "approval_request_id": ctx.approval_id,
            "recovery_key": binding.recovery_key,
            "recovery_depth": 1,
        }
        denied_strategy_state = _transitioned_recovery_strategy(
            source_task,
            status="denied",
            reason_code="recovery_plan_denied",
        )
        source_transitioned = self._conditional_update(
            binding.source_task_id,
            "needs_review",
            expected_statuses={current_source_status},
            force=False,
            status_reason_code="recovery_plan_denied",
            verification_status={
                **_mapping(getattr(source_task, "verification_status", None)),
                "model_recovery": denied_recovery_state,
                "model_recovery_strategy": (denied_strategy_state),
            },
            status_reason_details={
                **_mapping(getattr(source_task, "status_reason_details", None)),
                "model_recovery": denied_recovery_state,
                "model_recovery_strategy": (denied_strategy_state),
            },
            event_type="task_recovery_plan_denied",
            event_actor="hub_approval_dispatcher",
            event_details={"plan_id": binding.plan_id},
        )
        if source_transitioned or _denial_confirmed(ctx):
            return None
        return {
            "status": "failed",
            "reason_code": ("recovery_source_transition_conflict"),
            "plan_id": binding.plan_id,
        }

    def _stop_on_stale_recovery_state(self, ctx: _DecisionContext) -> dict[str, Any] | None:
        goal_terminal = is_terminal(ctx.goal, _TERMINAL_GOAL_STATUSES)
        source_status = str(getattr(ctx.source_task, "status", "") or "").strip().lower()
        source_terminal = source_status in _TERMINAL_TASK_STATUSES
        source_state_changed = source_status not in {"waiting_for_review", "needs_review"}
        if not (goal_terminal or source_terminal or source_state_changed):
            return None
        reason_code = (
            "recovery_goal_terminal"
            if goal_terminal
            else ("recovery_source_terminal" if source_terminal else "recovery_source_state_changed")
        )
        if ctx.decision == "granted":
            reject_plan(ctx.repos, ctx.plan, reason_code=reason_code)
            consumed = ctx.approval_service.consume_request(ctx.approval_id)
            if consumed is not None:
                return {
                    "status": "stopped",
                    "reason_code": reason_code,
                    "plan_id": ctx.binding.plan_id,
                    "approval_status": "consumed",
                }
        return {
            "status": "failed",
            "reason_code": reason_code,
        }

    def _stop_on_policy_change(self, ctx: _DecisionContext) -> dict[str, Any] | None:
        (
            current_actions,
            current_approval_required,
            current_policy_hash,
        ) = self._policy_binding(ctx.source_task)
        if (
            plan_action_configured(current_actions)
            and "require_approval" in current_actions
            and current_approval_required
            and current_policy_hash == ctx.binding.policy_hash
        ):
            return None
        return self._reject_and_consume(ctx, "recovery_policy_changed")

    def _refresh_on_plan_digest_drift(self, ctx: _DecisionContext) -> dict[str, Any] | None:
        binding = ctx.binding
        current_digest = plan_digest(ctx.plan, ctx.nodes)
        if (
            current_digest == ctx.expected_digest
            and str(getattr(ctx.approval, "target_fingerprint", "") or "") == current_digest
            and str(ctx.rationale.get("plan_digest") or "") == current_digest
        ):
            return None
        policy_hash = str(ctx.rationale.get("policy_hash") or "").strip()
        if not policy_hash:
            return {
                "status": "failed",
                "reason_code": "recovery_policy_binding_missing",
            }
        return self._saga.refresh_stale_plan_approval(
            repos=ctx.repos,
            plan_id=binding.plan_id,
            goal_id=binding.goal_id,
            source_task_id=binding.source_task_id,
            recovery_key=binding.recovery_key,
            policy_hash=policy_hash,
            team_id=binding.team_id,
            stale_approval_id=ctx.approval_id,
        )

    def _stop_on_reloaded_recovery_state(self, ctx: _DecisionContext) -> dict[str, Any] | None:
        binding = ctx.binding
        ctx.source_task = ctx.repos.task_repo.get_by_id(binding.source_task_id)
        ctx.goal = ctx.repos.goal_repo.get_by_id(binding.goal_id)
        reason_code = _reloaded_state_stop_reason(ctx.source_task, ctx.goal)
        if reason_code is None:
            return None
        return self._reject_and_consume(ctx, reason_code)

    def _stop_on_materialization_inputs_change(self, ctx: _DecisionContext) -> dict[str, Any] | None:
        approved_materialization_inputs_digest = str(ctx.rationale.get("materialization_inputs_digest") or "")
        current_materialization_inputs_digest = calculate_recovery_materialization_inputs_digest(ctx.goal)
        if (
            approved_materialization_inputs_digest
            and approved_materialization_inputs_digest == current_materialization_inputs_digest
        ):
            return None
        return self._reject_and_consume(ctx, "recovery_materialization_inputs_changed")

    def _materialize_and_release(self, ctx: _DecisionContext) -> dict[str, Any]:
        binding = ctx.binding
        result = self._planning_service().materialize_existing_plan(
            planner=self._planner(),
            plan_id=binding.plan_id,
            approval_request_id=str(getattr(ctx.approval, "id", "") or ""),
            team_id=binding.team_id or None,
            parent_task_id=None,
            source_task_id=binding.source_task_id,
            expected_plan_digest=ctx.expected_digest,
            # Dependency reconciliation automatically releases root nodes
            # which have no ``depends_on`` entries.  ``paused`` is the
            # canonical inert state that remains non-dispatchable until
            # the exact approval has been consumed below.
            initial_task_status="paused",
        )
        if str(result.get("reason_code") or "") == "recovery_plan_digest_stale":
            return self._saga.refresh_stale_plan_approval(
                repos=ctx.repos,
                plan_id=binding.plan_id,
                goal_id=binding.goal_id,
                source_task_id=binding.source_task_id,
                recovery_key=binding.recovery_key,
                policy_hash=binding.policy_hash,
                team_id=binding.team_id,
                stale_approval_id=ctx.approval_id,
            )
        if str(result.get("status") or "") != "materialized":
            return dict(result)

        ctx.plan = ctx.repos.plan_repo.get_by_id(binding.plan_id) or ctx.plan
        ctx.nodes = ctx.repos.plan_node_repo.get_by_plan_id(binding.plan_id) or ctx.nodes
        ctx.plan.status = "materialized"
        created_task_ids = [str(value) for value in list(result.get("created_task_ids") or []) if str(value).strip()]
        failure = self._consume_or_fail(ctx, created_task_ids=created_task_ids)
        if failure is not None:
            return failure

        release_result = self._release_materialized(ctx, created_task_ids)
        self._audit(
            "task_recovery_plan_materialized",
            {
                "task_id": binding.source_task_id,
                "goal_id": binding.goal_id,
                "plan_id": binding.plan_id,
                "approval_request_id": str(getattr(ctx.approval, "id", "") or ""),
                "created_task_ids": created_task_ids,
            },
        )
        return {
            **dict(result),
            **release_result,
        }


@dataclass(frozen=True)
class _ApprovalBinding:
    """The exact plan binding carried by a recovery approval's canonical arguments."""

    plan_id: str
    goal_id: str
    source_task_id: str
    recovery_key: str
    policy_hash: str
    team_id: str

    @classmethod
    def from_arguments(cls, args: dict) -> _ApprovalBinding | None:
        binding = cls(
            plan_id=str(args.get("plan_id") or "").strip(),
            goal_id=str(args.get("goal_id") or "").strip(),
            source_task_id=str(args.get("source_task_id") or "").strip(),
            recovery_key=str(args.get("recovery_key") or "").strip(),
            policy_hash=str(args.get("policy_hash") or "").strip(),
            team_id=str(args.get("team_id") or "").strip(),
        )
        required = (binding.plan_id, binding.goal_id, binding.source_task_id, binding.recovery_key, binding.policy_hash)
        if not all(required) or "team_id" not in args:
            return None
        return binding


@dataclass
class _DecisionContext:
    """Mutable view of the bound plan, source task and goal while one decision is applied."""

    binding: _ApprovalBinding
    args: dict
    approval: Any
    approval_id: str
    decision: str
    approval_service: Any
    repos: Any
    plan: Any
    nodes: Any
    source_task: Any
    goal: Any
    rationale: dict = field(default_factory=dict)
    source_recovery: dict = field(default_factory=dict)

    @property
    def expected_digest(self) -> str:
        return str(self.args.get("plan_digest") or "")

    def refresh_plan_views(self) -> None:
        self.rationale = _mapping(getattr(self.plan, "rationale", None))
        source_details = _mapping(getattr(self.source_task, "status_reason_details", None))
        self.source_recovery = _mapping(source_details.get("model_recovery"))


def _plan_binding_matches(ctx: _DecisionContext) -> bool:
    binding = ctx.binding
    rationale = ctx.rationale
    return (
        str(getattr(ctx.plan, "goal_id", "") or "") == binding.goal_id
        and str(getattr(ctx.source_task, "goal_id", "") or "") == binding.goal_id
        and str(rationale.get("recovery_key") or "") == binding.recovery_key
        and str(rationale.get("policy_hash") or "") == binding.policy_hash
        and str(rationale.get("source_task_id") or "") == binding.source_task_id
        and str(rationale.get("approval_request_id") or "") == ctx.approval_id
        and stored_team_binding_matches(rationale, binding.team_id)
    )


def _source_binding_matches(ctx: _DecisionContext) -> bool:
    source_recovery = ctx.source_recovery
    return (
        str(source_recovery.get("recovery_key") or "") == ctx.binding.recovery_key
        and str(source_recovery.get("plan_id") or "") == ctx.binding.plan_id
        and str(source_recovery.get("approval_request_id") or "") == ctx.approval_id
        and stored_team_binding_matches(source_recovery, ctx.binding.team_id)
    )


def _denial_confirmed(ctx: _DecisionContext) -> bool:
    """A lost denial CAS is fine when the source is terminal or already carries this denial."""
    confirmed_source = ctx.repos.task_repo.get_by_id(ctx.binding.source_task_id)
    confirmed_recovery = _mapping(
        _mapping(getattr(confirmed_source, "status_reason_details", None)).get("model_recovery")
    )
    return bool(
        confirmed_source is not None
        and (
            is_terminal(confirmed_source, _TERMINAL_TASK_STATUSES)
            or (
                str(confirmed_recovery.get("status") or "") == "denied"
                and str(confirmed_recovery.get("approval_request_id") or "") == ctx.approval_id
            )
        )
    )


def _reloaded_state_stop_reason(source_task: Any, goal: Any) -> str | None:
    if (
        source_task is not None
        and goal is not None
        and not is_terminal(source_task, _TERMINAL_TASK_STATUSES)
        and not is_terminal(goal, _TERMINAL_GOAL_STATUSES)
        and str(getattr(source_task, "status", "") or "").strip().lower() in {"waiting_for_review", "needs_review"}
    ):
        return None
    if goal is None or is_terminal(goal, _TERMINAL_GOAL_STATUSES):
        return "recovery_goal_terminal"
    if source_task is None or is_terminal(source_task, _TERMINAL_TASK_STATUSES):
        return "recovery_source_terminal"
    return "recovery_source_state_changed"


def _first_outcome(ctx: _DecisionContext, *stages: Callable[[_DecisionContext], dict[str, Any] | None]):
    """Run decision stages in order; return the first outcome (``None`` means continue)."""
    for stage in stages:
        outcome = stage(ctx)
        if outcome is not None:
            return outcome
    return None
