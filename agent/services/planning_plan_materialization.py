"""Plan-to-task materialization for ``PlanningService``.

Split out of ``planning_service`` (SRP): staging deterministic task ids,
resuming provably identical partial materializations, creating Hub tasks from
plan nodes and rolling back failed attempts.

``PlanMaterializer`` depends only on the narrow ports in
``PlanMaterializationPorts`` (DIP/ISP): a repository provider, a task
lifecycle provider, the exact-plan mutation lock and the pre-materialization
validator. ``PlanningService`` is the composition root that builds the ports
and keeps its historic method names as thin delegators.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from flask import current_app

from agent.db_models import PlanDB, PlanNodeDB
from agent.services.planning_materialization_validation import (
    dependency_contract_error,
    task_matches_materialization_binding,
)
from agent.services.recovery_plan_contract import calculate_recovery_plan_digest
from agent.services.task_dependency_policy import normalize_depends_on, validate_dependency_graph

_log = logging.getLogger("agent.services.planning_service")


class PlanTaskMaterializer(Protocol):
    """The lifecycle port that creates one Hub task from one plan node."""

    def materialize_from_plan_node(self, **values: Any) -> Any: ...


class ExistingPlanValidator(Protocol):
    def __call__(
        self,
        *,
        plan: PlanDB,
        nodes: list[PlanNodeDB],
        team_id: str | None,
    ) -> dict[str, Any]: ...


class PlanMutationLock(Protocol):
    def __call__(self, plan_id: str) -> AbstractContextManager[bool]: ...


@dataclass(frozen=True)
class PlanMaterializationPorts:
    """Exactly the collaborators plan materialization uses.

    ``repositories`` must return a registry exposing ``goal_repo``,
    ``plan_repo``, ``plan_node_repo`` and ``task_repo``; it and
    ``task_lifecycle`` are resolved lazily at the points the historic code
    resolved them.
    """

    repositories: Callable[[], Any]
    task_lifecycle: Callable[[], PlanTaskMaterializer]
    plan_mutation_lock: PlanMutationLock
    validate_existing_plan: ExistingPlanValidator
    pipeline_shell_modes: frozenset[str] = frozenset()


def prepare_materialization(
    nodes: list[PlanNodeDB],
    *,
    deterministic_seed: str | None = None,
) -> list[dict[str, Any]] | None:
    """Stage task ids and dependencies of a plan DAG, or ``None`` if invalid."""
    if dependency_contract_error(nodes) is not None:
        return None
    if deterministic_seed:
        node_to_task_id = {
            node.node_key: (
                "goal-"
                + hashlib.sha256(
                    (
                        str(deterministic_seed)
                        + "\x00"
                        + str(node.id)
                        + "\x00"
                        + str(node.node_key)
                    ).encode("utf-8")
                ).hexdigest()[:16]
            )
            for node in nodes
        }
    else:
        node_to_task_id = {
            node.node_key: f"goal-{uuid.uuid4().hex[:8]}"
            for node in nodes
        }
    staged: list[dict[str, Any]] = []
    created_order: list[str] = []
    staged_graph: dict[str, list[str]] = {}
    for node in nodes:
        task_id = node_to_task_id[node.node_key]
        task_depends_on = []
        is_parallel_node = bool((node.rationale or {}).get("parallel"))
        dependency_mode = str((node.rationale or {}).get("dependency_mode") or "").strip().lower()
        if node.depends_on:
            task_depends_on = [
                node_to_task_id[dep] for dep in node.depends_on
            ]
        elif dependency_mode == "sequential" and not is_parallel_node and created_order:
            task_depends_on = created_order[-1:]
        task_depends_on = normalize_depends_on(task_depends_on, task_id)
        staged_graph[task_id] = task_depends_on
        staged.append({"node": node, "task_id": task_id, "depends_on": task_depends_on})
        created_order.append(task_id)
    ok, _reason = validate_dependency_graph(staged_graph)
    if not ok:
        return None
    return staged


class PlanMaterializer:
    """Turn persisted or in-memory plans into Hub tasks through explicit ports."""

    def __init__(self, ports: PlanMaterializationPorts) -> None:
        self._ports = ports

    def materialize_plan(
        self,
        planner,
        plan: PlanDB | None,
        nodes: list[PlanNodeDB],
        team_id: Optional[str],
        parent_task_id: Optional[str],
        goal_id: Optional[str],
        goal_trace_id: Optional[str],
        mode: str = "generic",
        deterministic_task_ids: bool = False,
        source_task_id: Optional[str] = None,
        initial_task_status: str = "todo",
    ) -> tuple[list[str], str | None]:
        repos = self._ports.repositories()
        if goal_id:
            authoritative_goal = repos.goal_repo.get_by_id(
                str(goal_id)
            )
            from agent.services.organization_planning_adapter import (
                is_organization_goal,
            )

            if is_organization_goal(authoritative_goal):
                return [], "organization_planning_legacy_bypass_blocked"
            if (
                authoritative_goal is None
                or str(
                    getattr(
                        authoritative_goal,
                        "status",
                        "",
                    )
                    or ""
                )
                .strip()
                .lower()
                in {
                    "completed",
                    "failed",
                    "cancelled",
                    "aborted",
                    "timeout",
                    "archived",
                }
            ):
                return [], "goal_terminal"
        staged = prepare_materialization(
            nodes=nodes,
            deterministic_seed=(
                str(getattr(plan, "id", "") or "")
                if deterministic_task_ids and plan is not None
                else None
            ),
        )
        if staged is None:
            if plan:
                plan.status = "failed"
                plan.rationale = {**(plan.rationale or {}), "materialization_error": "invalid_dependencies"}
                plan.updated_at = time.time()
                repos.plan_repo.save(plan)
            return [], "invalid_dependencies"

        # Inject shell_command_mode into node rationale for repair/pipeline modes
        if mode in self._ports.pipeline_shell_modes:
            for node in nodes:
                node.rationale = {**(node.rationale or {}), "shell_command_mode": "pipeline"}

        existing_ids: list[str] = []
        entries_to_create = list(staged)
        if deterministic_task_ids and plan is not None:
            (
                existing_ids,
                entries_to_create,
                binding_error,
            ) = self.classify_deterministic_materialization(
                plan=plan,
                staged=staged,
                team_id=team_id,
                parent_task_id=parent_task_id,
                source_task_id=source_task_id,
                initial_task_status=initial_task_status,
            )
            if binding_error is not None:
                plan.status = "failed"
                plan.rationale = {
                    **dict(plan.rationale or {}),
                    "materialization_error": binding_error,
                }
                plan.updated_at = time.time()
                repos.plan_repo.save(plan)
                return [], binding_error

        newly_created_ids: list[str] = []
        try:
            for entry in entries_to_create:
                node = entry["node"]
                task_id = entry["task_id"]
                task_depends_on = entry["depends_on"]
                self._ports.task_lifecycle().materialize_from_plan_node(
                    task_id=task_id,
                    node=node,
                    team_id=team_id,
                    goal_id=goal_id,
                    goal_trace_id=goal_trace_id,
                    plan_id=plan.id if plan else None,
                    parent_task_id=parent_task_id,
                    derivation_reason=f"goal_{plan.planning_mode if plan else 'planning'}",
                    derivation_depth=1 if (parent_task_id or source_task_id) else 0,
                    depends_on=task_depends_on,
                    source_task_id=source_task_id,
                    initial_status=initial_task_status,
                )
                newly_created_ids.append(task_id)
                node.materialized_task_id = task_id
                node.status = "materialized"
                node.updated_at = time.time()
                # A plan node is only persistable when its plan is: plan_nodes
                # carries a foreign key to plans.id, and without a persisted
                # plan the pipeline builds nodes under a stand-in id that
                # references nothing. Without a plan these nodes are transient
                # working state, so they are kept in memory and not written.
                if plan is not None:
                    repos.plan_node_repo.save(node)
                planner._stats["tasks_created"] += 1
        except Exception as exc:
            _log.warning("Plan materialization failed for plan %s: %s", plan.id if plan else "ad-hoc", exc)
            self.rollback_materialization(
                plan=plan,
                nodes=nodes,
                created_ids=newly_created_ids,
                error=str(exc),
            )
            return [], "materialization_failed"

        materialized_ids = [
            str(entry["task_id"])
            for entry in staged
            if str(entry["task_id"]) in set(existing_ids + newly_created_ids)
        ]
        if plan:
            plan.status = "materialized" if materialized_ids else "draft"
            plan.updated_at = time.time()
            repos.plan_repo.save(plan)
        return materialized_ids, None

    def materialize_existing_plan(
        self,
        *,
        planner,
        plan_id: str,
        approval_request_id: str,
        team_id: str | None = None,
        parent_task_id: str | None = None,
        source_task_id: str | None = None,
        expected_plan_digest: str | None = None,
        initial_task_status: str = "todo",
    ) -> dict[str, Any]:
        """Materialize one persisted draft after a Hub approval was granted.

        Approval validation deliberately remains outside this service.  This
        method owns only the planning-domain transition from a validated,
        persisted plan to Hub tasks, making the mutation reusable without
        exposing ``_materialize_plan`` to routes or approval infrastructure.
        """
        normalized_plan_id = str(plan_id or "").strip()
        normalized_approval_id = str(approval_request_id or "").strip()
        normalized_initial_status = str(
            initial_task_status or "todo"
        ).strip().lower()
        if not normalized_plan_id:
            return {"status": "failed", "reason_code": "plan_id_required", "created_task_ids": []}
        if not normalized_approval_id:
            return {"status": "failed", "reason_code": "approval_request_id_required", "created_task_ids": []}
        if normalized_initial_status not in {
            "todo",
            "blocked_by_dependency",
            "paused",
        }:
            return {
                "status": "failed",
                "reason_code": "initial_task_status_invalid",
                "created_task_ids": [],
            }

        with self._ports.plan_mutation_lock(
            normalized_plan_id
        ) as distributed_lock_acquired:
            if not distributed_lock_acquired:
                return {
                    "status": "failed",
                    "reason_code": "plan_mutation_in_progress",
                    "plan_id": normalized_plan_id,
                    "created_task_ids": [],
                }
            repos = self._ports.repositories()
            plan = repos.plan_repo.get_by_id(normalized_plan_id)
            if plan is None:
                return {"status": "failed", "reason_code": "plan_not_found", "created_task_ids": []}
            if str(
                dict(plan.rationale or {}).get("approval_request_id") or ""
            ) != normalized_approval_id:
                return {
                    "status": "failed",
                    "reason_code": "plan_approval_binding_mismatch",
                    "plan_id": plan.id,
                    "created_task_ids": [],
                }
            already_materialized = (
                str(plan.status or "") == "materialized"
            )
            if (
                not already_materialized
                and str(plan.status or "")
                not in {"draft", "pending_approval", "approved"}
            ):
                return {
                    "status": "failed",
                    "reason_code": f"plan_status_not_materializable:{plan.status}",
                    "plan_id": plan.id,
                    "created_task_ids": [],
                }

            nodes = repos.plan_node_repo.get_by_plan_id(plan.id)
            if not nodes:
                return {
                    "status": "failed",
                    "reason_code": "plan_nodes_missing",
                    "plan_id": plan.id,
                    "created_task_ids": [],
                }
            normalized_expected_digest = str(
                expected_plan_digest or ""
            ).strip()
            if normalized_expected_digest:
                current_digest = calculate_recovery_plan_digest(plan, nodes)
                bound_digest = str(
                    dict(plan.rationale or {}).get("plan_digest") or ""
                ).strip()
                if (
                    current_digest != normalized_expected_digest
                    or bound_digest != normalized_expected_digest
                ):
                    return {
                        "status": "failed",
                        "reason_code": "recovery_plan_digest_stale",
                        "plan_id": plan.id,
                        "current_plan_digest": current_digest,
                        "created_task_ids": [],
                    }

            if already_materialized:
                staged = prepare_materialization(
                    nodes=nodes,
                    deterministic_seed=str(plan.id),
                )
                if staged is None:
                    return {
                        "status": "failed",
                        "reason_code": "invalid_dependencies",
                        "plan_id": plan.id,
                        "created_task_ids": [],
                    }
                (
                    existing_ids,
                    missing_entries,
                    binding_error,
                ) = self.classify_deterministic_materialization(
                    plan=plan,
                    staged=staged,
                    team_id=team_id,
                    parent_task_id=parent_task_id,
                    source_task_id=source_task_id,
                    initial_task_status=normalized_initial_status,
                )
                if binding_error is not None or missing_entries:
                    return {
                        "status": "failed",
                        "reason_code": (
                            binding_error
                            or "materialized_tasks_missing"
                        ),
                        "plan_id": plan.id,
                        "created_task_ids": [],
                    }
                return {
                    "status": "materialized",
                    "reason_code": "already_materialized",
                    "plan_id": plan.id,
                    "created_task_ids": existing_ids,
                }

            validation = self._ports.validate_existing_plan(
                plan=plan,
                nodes=nodes,
                team_id=team_id,
            )
            if not validation["ok"]:
                plan.status = "draft"
                plan.rationale = {
                    **dict(plan.rationale or {}),
                    "approval_state": "validation_failed",
                    "materialization_validation": validation,
                }
                plan.updated_at = time.time()
                repos.plan_repo.save(plan)
                return {
                    "status": "failed",
                    "reason_code": str(validation["reason_code"]),
                    "plan_id": plan.id,
                    "created_task_ids": [],
                    "validation": validation,
                }

            plan.status = "approved"
            plan.rationale = {
                **dict(plan.rationale or {}),
                "approval_request_id": normalized_approval_id,
                "approval_state": "granted",
                "materialization_validation": validation,
            }
            plan.updated_at = time.time()
            plan = repos.plan_repo.save(plan)
            created_ids, error = self.materialize_plan(
                planner=planner,
                plan=plan,
                nodes=nodes,
                team_id=team_id,
                parent_task_id=parent_task_id,
                goal_id=plan.goal_id,
                goal_trace_id=plan.trace_id,
                mode=str(plan.planning_mode or "generic"),
                deterministic_task_ids=True,
                source_task_id=source_task_id,
                initial_task_status=normalized_initial_status,
            )
            if error:
                retryable = self.restore_retryable_materialization_state(
                    plan_id=plan.id,
                    error=error,
                    team_id=team_id,
                    parent_task_id=parent_task_id,
                    source_task_id=source_task_id,
                    initial_task_status=normalized_initial_status,
                )
                return {
                    "status": "failed",
                    "reason_code": error,
                    "plan_id": plan.id,
                    "created_task_ids": [],
                    "retryable": retryable,
                }
            return {
                "status": "materialized",
                "reason_code": "approved_plan_materialized",
                "plan_id": plan.id,
                "created_task_ids": created_ids,
            }

    def classify_deterministic_materialization(
        self,
        *,
        plan: PlanDB,
        staged: list[dict[str, Any]],
        team_id: str | None,
        parent_task_id: str | None,
        source_task_id: str | None,
        initial_task_status: str,
    ) -> tuple[list[str], list[dict[str, Any]], str | None]:
        """Resume only a provably identical deterministic partial DAG."""

        repos = self._ports.repositories()
        existing_ids: list[str] = []
        entries_to_create: list[dict[str, Any]] = []
        for entry in staged:
            node = entry["node"]
            task_id = str(entry["task_id"])
            bound_task_id = str(
                getattr(node, "materialized_task_id", "") or ""
            ).strip()
            if bound_task_id and bound_task_id != task_id:
                return [], [], "materialization_binding_conflict"

            task = repos.task_repo.get_by_id(task_id)
            if task is None:
                if bound_task_id:
                    return [], [], "materialization_binding_conflict"
                entries_to_create.append(entry)
                continue
            if not task_matches_materialization_binding(
                task,
                plan=plan,
                node=node,
                task_id=task_id,
                team_id=team_id,
                parent_task_id=parent_task_id,
                source_task_id=source_task_id,
                depends_on=list(entry["depends_on"] or []),
                initial_task_status=initial_task_status,
            ):
                return [], [], "materialization_binding_conflict"
            if not bound_task_id:
                # Crash window: task commit succeeded, node commit did not.
                node.materialized_task_id = task_id
                node.status = "materialized"
                node.updated_at = time.time()
                repos.plan_node_repo.save(node)
            existing_ids.append(task_id)
        return existing_ids, entries_to_create, None

    def restore_retryable_materialization_state(
        self,
        *,
        plan_id: str,
        error: str,
        team_id: str | None,
        parent_task_id: str | None,
        source_task_id: str | None,
        initial_task_status: str,
    ) -> bool:
        """Re-open a plan when every deterministic survivor is exact."""

        if str(error or "") != "materialization_failed":
            return False
        repos = self._ports.repositories()
        plan = repos.plan_repo.get_by_id(plan_id)
        nodes = repos.plan_node_repo.get_by_plan_id(plan_id)
        if plan is None or not nodes:
            return False
        staged = prepare_materialization(
            nodes=nodes,
            deterministic_seed=str(plan.id),
        )
        if staged is None:
            return False
        (
            existing_ids,
            _missing_entries,
            binding_error,
        ) = self.classify_deterministic_materialization(
            plan=plan,
            staged=staged,
            team_id=team_id,
            parent_task_id=parent_task_id,
            source_task_id=source_task_id,
            initial_task_status=initial_task_status,
        )
        if binding_error is not None:
            return False
        plan.status = "pending_approval"
        plan.rationale = {
            **dict(plan.rationale or {}),
            "approval_state": "materialization_retryable",
            "materialization_error": str(error)[:500],
            "materialization_survivor_task_ids": list(
                existing_ids
            ),
        }
        plan.updated_at = time.time()
        repos.plan_repo.save(plan)
        return True

    def rollback_materialization(self, plan: PlanDB | None, nodes: list[PlanNodeDB], created_ids: list[str], error: str) -> None:
        repos = self._ports.repositories()
        for task_id in created_ids:
            try:
                repos.task_repo.delete(task_id)
            except Exception:
                current_app.logger.warning("Failed to rollback task %s after materialization failure", task_id)
        for node in nodes:
            if node.materialized_task_id in created_ids:
                node.materialized_task_id = None
                node.status = "pending"
                node.updated_at = time.time()
                # Same rule as on the way in. Without this the rollback raised
                # a second foreign-key error while cleaning up after the first,
                # which is what surfaced in CI as the unhandled exception.
                if plan is not None:
                    repos.plan_node_repo.save(node)
        if plan:
            plan.status = "failed"
            plan.rationale = {
                **(plan.rationale or {}),
                "materialization_error": error[:500],
            }
            plan.updated_at = time.time()
            repos.plan_repo.save(plan)
