"""Release of materialized recovery plans back into the Hub task queue.

Split out of ``task_recovery_planning_service`` (SRP): persisting the release
state and epoch, cancelling superseded recovery children and releasing an
approved, materialized recovery plan exactly once. :class:`RecoveryRelease`
receives only the lock bundle, the task CAS port and a child-cancellation
strategy (dispatch-gate invalidation in production, status CAS when a task
status port is injected); ``TaskRecoveryPlanningService`` composes it and
keeps its historic private method names as thin delegators.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Protocol

from agent.services.recovery_plan_contract import (
    build_recovery_dependency_binding,
    calculate_recovery_task_payload_digest,
)
from agent.services.task_recovery_planning_rules import (
    _TERMINAL_GOAL_STATUSES,
    _TERMINAL_TASK_STATUSES,
    RECOVERY_STATE_SCHEMA,
    is_terminal,
    team_binding_matches,
)
from agent.services.task_recovery_planning_values import mapping as _mapping
from agent.services.task_recovery_planning_values import sha256_json as _sha256_json
from agent.services.task_recovery_planning_values import (
    transitioned_recovery_strategy as _transitioned_recovery_strategy,
)
from agent.services.task_recovery_ports import ConditionalTaskUpdate, RecoveryLocks


def release_epoch(
    *,
    plan_id: str,
    approval_id: str,
    recovery_key: str,
    team_id: str,
) -> str:
    return _sha256_json(
        {
            "schema": "ananta.recovery_release_epoch.v1",
            "plan_id": plan_id,
            "approval_id": approval_id,
            "recovery_key": recovery_key,
            "team_id": team_id,
        }
    )


_compute_release_epoch = release_epoch

_ACTIVE_CHILD_STATUSES = frozenset(
    {
        "todo",
        "created",
        "assigned",
        "proposing",
        "in_progress",
        "delegated",
        "waiting_for_review",
        "needs_review",
        "blocked",
        "blocked_by_dependency",
        "paused",
        "updated",
    }
)


class RecoveryChildCanceller(Protocol):
    """Stop superseded recovery children of one plan (strategy port)."""

    def cancel(
        self,
        *,
        created_task_ids: list[str],
        plan_id: str,
        source_task_id: str,
    ) -> None: ...


class DispatchGateChildCanceller:
    """Production strategy: invalidate the children at the recovery dispatch gate."""

    def __init__(self, gate_provider: Callable[[], Any] | None = None) -> None:
        self._gate_provider = gate_provider

    def _gate(self) -> Any:
        if self._gate_provider is not None:
            return self._gate_provider()
        from agent.services.recovery_dispatch_gate_service import (
            get_recovery_dispatch_gate_service,
        )

        return get_recovery_dispatch_gate_service()

    def cancel(
        self,
        *,
        created_task_ids: list[str],
        plan_id: str,
        source_task_id: str,
    ) -> None:
        recovery_gate = self._gate()
        for child_task_id in created_task_ids:
            recovery_gate.invalidate_task(
                child_task_id,
                reason_code="recovery_parent_state_changed",
            )


class StatusCasChildCanceller:
    """Strategy for an injected task-status port: CAS every active child to ``cancelled``."""

    def __init__(self, conditional_update: ConditionalTaskUpdate) -> None:
        self._conditional_update = conditional_update

    def cancel(
        self,
        *,
        created_task_ids: list[str],
        plan_id: str,
        source_task_id: str,
    ) -> None:
        for child_task_id in created_task_ids:
            self._conditional_update(
                child_task_id,
                "cancelled",
                expected_statuses=set(_ACTIVE_CHILD_STATUSES),
                force=False,
                status_reason_code="recovery_parent_state_changed",
                event_type="task_recovery_child_cancelled",
                event_actor="hub_approval_dispatcher",
                event_details={
                    "plan_id": plan_id,
                    "source_task_id": source_task_id,
                },
            )


class RecoveryRelease:
    """Release one approved, materialized recovery DAG exactly once."""

    def __init__(
        self,
        *,
        locks: RecoveryLocks,
        conditional_update: ConditionalTaskUpdate,
        child_canceller: RecoveryChildCanceller,
    ) -> None:
        self._locks = locks
        self._conditional_update = conditional_update
        self._child_canceller = child_canceller

    def save_release_state(
        self,
        *,
        repos: Any,
        plan_id: str,
        state: str,
        release_epoch: str | None = None,
        release_details: dict[str, Any] | None = None,
    ) -> bool:
        with self._locks.plan_mutation_lock(plan_id) as acquired:
            if not acquired:
                return False
            plan = repos.plan_repo.get_by_id(plan_id)
            if plan is None:
                return False
            rationale = _mapping(getattr(plan, "rationale", None))
            current_state = str(
                rationale.get("materialization_release_state") or ""
            )
            current_epoch = str(
                rationale.get("materialization_release_epoch") or ""
            )
            normalized_epoch = str(release_epoch or "")
            if current_state == "cancelled" and state != "cancelled":
                return False
            if (
                current_epoch
                and normalized_epoch
                and current_epoch != normalized_epoch
            ):
                return False
            if current_state == "completed" and state == "committed":
                return True
            plan.rationale = {
                **rationale,
                **dict(release_details or {}),
                "materialization_release_state": state,
                **(
                    {
                        "materialization_release_epoch": (
                            normalized_epoch
                        )
                    }
                    if normalized_epoch
                    else {}
                ),
            }
            plan.updated_at = time.time()
            repos.plan_repo.save(plan)
            return True


    def cancel_recovery_children(
        self,
        *,
        created_task_ids: list[str],
        plan_id: str,
        source_task_id: str,
    ) -> None:
        self._child_canceller.cancel(
            created_task_ids=created_task_ids,
            plan_id=plan_id,
            source_task_id=source_task_id,
        )

    def release_materialized_recovery(
        self,
        *,
        repos: Any,
        plan: Any,
        nodes: list[Any],
        source_task_id: str,
        goal_id: str,
        approval_id: str,
        recovery_key: str,
        team_id: str,
        created_task_ids: list[str],
    ) -> dict[str, Any]:
        """Idempotently release a DAG only after a confirmed source CAS."""

        plan_id = str(getattr(plan, "id", "") or "")
        release_epoch = _compute_release_epoch(
            plan_id=plan_id,
            approval_id=approval_id,
            recovery_key=recovery_key,
            team_id=team_id,
        )
        with self._locks.distributed_task_locks(
            {source_task_id, *created_task_ids}
        ) as source_lock_acquired:
            if not source_lock_acquired:
                return {
                    "status": "ignored",
                    "reason_code": "recovery_action_in_progress",
                    "plan_id": plan_id,
                }
            with self._locks.source_mutation_lock(source_task_id):
                latest_source = repos.task_repo.get_by_id(source_task_id)
                latest_goal = repos.goal_repo.get_by_id(goal_id)
                source_status = str(getattr(latest_source, "status", "") or "").strip().lower()
                source_terminal = latest_source is None or is_terminal(
                    latest_source,
                    _TERMINAL_TASK_STATUSES,
                )
                goal_terminal = latest_goal is None or is_terminal(
                    latest_goal,
                    _TERMINAL_GOAL_STATUSES,
                )
                team_binding_changed = not (
                    latest_source is not None
                    and latest_goal is not None
                    and team_binding_matches(
                        source_task=latest_source,
                        goal=latest_goal,
                        expected_team_id=team_id,
                    )
                )
                source_state_changed = not source_terminal and source_status not in {
                    "waiting_for_review",
                    "needs_review",
                    "blocked_by_dependency",
                }
                if (
                    source_terminal
                    or source_state_changed
                    or goal_terminal
                    or team_binding_changed
                ):
                    self.cancel_recovery_children(
                        created_task_ids=created_task_ids,
                        plan_id=plan_id,
                        source_task_id=source_task_id,
                    )
                    self.save_release_state(
                        repos=repos,
                        plan_id=plan_id,
                        state="cancelled",
                        release_epoch=release_epoch,
                    )
                    return {
                        "status": "materialized",
                        "reason_code": (
                            "recovery_goal_became_terminal"
                            if goal_terminal
                            else (
                                "recovery_source_terminal"
                                if source_terminal
                                else (
                                    "recovery_team_binding_changed"
                                    if team_binding_changed
                                    else "recovery_source_state_changed"
                                )
                            )
                        ),
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                        "approval_status": "consumed",
                        "children_cancelled": True,
                    }

                authoritative_plan = repos.plan_repo.get_by_id(plan_id)
                authoritative_rationale = _mapping(
                    getattr(authoritative_plan, "rationale", None)
                )
                release_state = str(
                    authoritative_rationale.get(
                        "materialization_release_state"
                    )
                    or ""
                )
                authoritative_epoch = str(
                    authoritative_rationale.get(
                        "materialization_release_epoch"
                    )
                    or ""
                )
                if release_state == "cancelled":
                    return {
                        "status": "materialized",
                        "reason_code": "recovery_release_cancelled",
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                        "approval_status": "consumed",
                        "children_cancelled": True,
                    }
                if release_state == "completed":
                    source_recovery = _mapping(
                        _mapping(
                            getattr(
                                latest_source,
                                "status_reason_details",
                                None,
                            )
                        ).get("model_recovery")
                    )
                    if (
                        source_status == "blocked_by_dependency"
                        and str(source_recovery.get("plan_id") or "") == plan_id
                        and str(source_recovery.get("approval_request_id") or "") == approval_id
                        and (
                            not authoritative_epoch
                            or (
                                authoritative_epoch
                                == release_epoch
                                == str(
                                    source_recovery.get(
                                        "release_epoch"
                                    )
                                    or ""
                                )
                            )
                        )
                    ):
                        return {
                            "status": "materialized",
                            "reason_code": ("recovery_release_already_completed"),
                            "plan_id": plan_id,
                            "created_task_ids": created_task_ids,
                            "approval_status": "consumed",
                        }
                    self.cancel_recovery_children(
                        created_task_ids=created_task_ids,
                        plan_id=plan_id,
                        source_task_id=source_task_id,
                    )
                    self.save_release_state(
                        repos=repos,
                        plan_id=plan_id,
                        state="cancelled",
                        release_epoch=release_epoch,
                    )
                    return {
                        "status": "materialized",
                        "reason_code": ("recovery_source_confirmation_failed"),
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                        "approval_status": "consumed",
                        "children_cancelled": True,
                    }

                node_task_pairs: list[tuple[Any, str]] = []
                for index, node in enumerate(nodes):
                    task_id = str(getattr(node, "materialized_task_id", "") or "").strip()
                    if not task_id and index < len(created_task_ids):
                        task_id = created_task_ids[index]
                    if task_id:
                        node_task_pairs.append((node, task_id))
                node_key_to_task_id = {
                    str(getattr(node, "node_key", "") or ""): task_id for node, task_id in node_task_pairs
                }
                child_dependencies = {
                    task_id: [
                        node_key_to_task_id[dependency]
                        for dependency in list(getattr(node, "depends_on", None) or [])
                        if dependency in node_key_to_task_id
                    ]
                    for node, task_id in node_task_pairs
                }
                expected_task_ids = [task_id for _node, task_id in node_task_pairs]
                if len(expected_task_ids) != len(created_task_ids) or set(expected_task_ids) != set(created_task_ids):
                    self.cancel_recovery_children(
                        created_task_ids=created_task_ids,
                        plan_id=plan_id,
                        source_task_id=source_task_id,
                    )
                    self.save_release_state(
                        repos=repos,
                        plan_id=plan_id,
                        state="cancelled",
                        release_epoch=release_epoch,
                    )
                    return {
                        "status": "failed",
                        "reason_code": "materialized_task_binding_mismatch",
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                        "children_cancelled": True,
                    }

                child_id_set = set(created_task_ids)
                preexisting_dependency_ids = list(
                    dict.fromkeys(
                        str(value or "").strip()
                        for value in list(
                            getattr(
                                latest_source,
                                "depends_on",
                                None,
                            )
                            or []
                        )
                        if (
                            str(value or "").strip()
                            and str(value or "").strip()
                            not in child_id_set
                        )
                    )
                )
                dependency_binding = (
                    build_recovery_dependency_binding(
                        source_task_id=source_task_id,
                        preexisting_dependency_ids=(
                            preexisting_dependency_ids
                        ),
                        child_task_ids=created_task_ids,
                    )
                )
                recovery_state = {
                    "schema": RECOVERY_STATE_SCHEMA,
                    "status": "materialized_waiting_for_children",
                    "plan_id": plan_id,
                    "approval_request_id": approval_id,
                    "created_task_ids": created_task_ids,
                    "recovery_key": recovery_key,
                    "recovery_depth": 1,
                    "release_epoch": release_epoch,
                    "team_id": team_id,
                    "dependency_binding": dependency_binding,
                }
                recovery_strategy_state = (
                    _transitioned_recovery_strategy(
                        latest_source,
                        status="materialized",
                        reason_code="approved_plan_materialized",
                    )
                )
                source_transitioned = self._conditional_update(
                    source_task_id,
                    "blocked_by_dependency",
                    expected_statuses={
                        "waiting_for_review",
                        "needs_review",
                        "blocked_by_dependency",
                    },
                    force=False,
                    status_reason_code=("recovery_plan_materialized_waiting_for_children"),
                    depends_on=list(
                        dependency_binding[
                            "authoritative_dependency_ids"
                        ]
                    ),
                    verification_status={
                        **_mapping(
                            getattr(
                                latest_source,
                                "verification_status",
                                None,
                            )
                        ),
                        "model_recovery": recovery_state,
                        "model_recovery_strategy": (
                            recovery_strategy_state
                        ),
                    },
                    status_reason_details={
                        **_mapping(
                            getattr(
                                latest_source,
                                "status_reason_details",
                                None,
                            )
                        ),
                        "model_recovery": recovery_state,
                        "model_recovery_strategy": (
                            recovery_strategy_state
                        ),
                    },
                    event_type="task_recovery_plan_materialized",
                    event_actor="hub_approval_dispatcher",
                    event_details={
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                    },
                )
                if not source_transitioned:
                    self.cancel_recovery_children(
                        created_task_ids=created_task_ids,
                        plan_id=plan_id,
                        source_task_id=source_task_id,
                    )
                    self.save_release_state(
                        repos=repos,
                        plan_id=plan_id,
                        state="cancelled",
                        release_epoch=release_epoch,
                    )
                    return {
                        "status": "materialized",
                        "reason_code": "recovery_source_transition_conflict",
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                        "approval_status": "consumed",
                        "children_cancelled": True,
                    }

                release_details = {
                    "materialization_release_source_task_id": (
                        source_task_id
                    ),
                    "materialization_release_goal_id": goal_id,
                    "materialization_release_approval_id": approval_id,
                    "materialization_release_team_id": team_id,
                    "materialization_dependency_binding_digest": (
                        dependency_binding["digest"]
                    ),
                }
                if not self.save_release_state(
                    repos=repos,
                    plan_id=plan_id,
                    state="committed",
                    release_epoch=release_epoch,
                    release_details=release_details,
                ):
                    self.cancel_recovery_children(
                        created_task_ids=created_task_ids,
                        plan_id=plan_id,
                        source_task_id=source_task_id,
                    )
                    return {
                        "status": "failed",
                        "reason_code": (
                            "recovery_release_commit_conflict"
                        ),
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                        "children_cancelled": True,
                    }

                for child_task_id in created_task_ids:
                    dependencies = child_dependencies.get(child_task_id)
                    release_status = "todo" if dependencies == [] else "blocked_by_dependency"
                    child = repos.task_repo.get_by_id(child_task_id)
                    child_release_state = {
                        "schema": "ananta.recovery_release_gate.v1",
                        "release_epoch": release_epoch,
                        "plan_id": plan_id,
                        "source_task_id": source_task_id,
                        "goal_id": goal_id,
                        "approval_request_id": approval_id,
                        "recovery_key": recovery_key,
                        "team_id": team_id,
                        "task_payload_digest": (
                            calculate_recovery_task_payload_digest(
                                child
                            )
                        ),
                    }
                    released = self._conditional_update(
                        child_task_id,
                        release_status,
                        expected_statuses={
                            "paused",
                            release_status,
                        },
                        force=False,
                        status_reason_code="recovery_approval_consumed",
                        status_reason_details={
                            **_mapping(
                                getattr(
                                    child,
                                    "status_reason_details",
                                    None,
                                )
                            ),
                            "model_recovery_release": (
                                child_release_state
                            ),
                        },
                        event_type="task_recovery_child_released",
                        event_actor="hub_approval_dispatcher",
                        event_details={
                            "plan_id": plan_id,
                            "source_task_id": source_task_id,
                        },
                    )
                    if not released:
                        child = repos.task_repo.get_by_id(child_task_id)
                        child_status = str(getattr(child, "status", "") or "").strip().lower()
                        # A released root may already be claimed before an
                        # interrupted release is reconciled.
                        released = bool(
                            child is not None
                            and str(getattr(child, "plan_id", "") or "") == plan_id
                            and child_status != "paused"
                            and str(
                                _mapping(
                                    _mapping(
                                        getattr(
                                            child,
                                            "status_reason_details",
                                            None,
                                        )
                                    ).get(
                                        "model_recovery_release"
                                    )
                                ).get("release_epoch")
                                or ""
                            )
                            == release_epoch
                        )
                    if not released:
                        self.cancel_recovery_children(
                            created_task_ids=created_task_ids,
                            plan_id=plan_id,
                            source_task_id=source_task_id,
                        )
                        self.save_release_state(
                            repos=repos,
                            plan_id=plan_id,
                            state="cancelled",
                            release_epoch=release_epoch,
                        )
                        return {
                            "status": "failed",
                            "reason_code": "recovery_child_release_conflict",
                            "plan_id": plan_id,
                            "created_task_ids": created_task_ids,
                            "children_cancelled": True,
                        }

                # Do not persist a successful release marker based on a stale
                # write attempt.  Re-read both authoritative owners.
                confirmed_source = repos.task_repo.get_by_id(source_task_id)
                confirmed_goal = repos.goal_repo.get_by_id(goal_id)
                confirmed_recovery = _mapping(
                    _mapping(
                        getattr(
                            confirmed_source,
                            "status_reason_details",
                            None,
                        )
                    ).get("model_recovery")
                )
                source_confirmed = bool(
                    confirmed_source is not None
                    and str(getattr(confirmed_source, "status", "") or "").strip().lower() == "blocked_by_dependency"
                    and str(confirmed_recovery.get("plan_id") or "") == plan_id
                    and str(confirmed_recovery.get("approval_request_id") or "") == approval_id
                    and str(
                        confirmed_recovery.get("release_epoch") or ""
                    )
                    == release_epoch
                    and team_binding_matches(
                        source_task=confirmed_source,
                        goal=confirmed_goal,
                        expected_team_id=team_id,
                    )
                )
                if (
                    not source_confirmed
                    or confirmed_goal is None
                    or is_terminal(
                        confirmed_goal,
                        _TERMINAL_GOAL_STATUSES,
                    )
                ):
                    self.cancel_recovery_children(
                        created_task_ids=created_task_ids,
                        plan_id=plan_id,
                        source_task_id=source_task_id,
                    )
                    self.save_release_state(
                        repos=repos,
                        plan_id=plan_id,
                        state="cancelled",
                        release_epoch=release_epoch,
                    )
                    return {
                        "status": "materialized",
                        "reason_code": "recovery_source_confirmation_failed",
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                        "approval_status": "consumed",
                        "children_cancelled": True,
                    }
                if not self.save_release_state(
                    repos=repos,
                    plan_id=plan_id,
                    state="completed",
                    release_epoch=release_epoch,
                    release_details=release_details,
                ):
                    return {
                        "status": "ignored",
                        "reason_code": "recovery_action_in_progress",
                        "plan_id": plan_id,
                        "created_task_ids": created_task_ids,
                    }
                return {
                    "status": "materialized",
                    "reason_code": "approved_plan_materialized",
                    "plan_id": plan_id,
                    "created_task_ids": created_task_ids,
                    "approval_status": "consumed",
                }
