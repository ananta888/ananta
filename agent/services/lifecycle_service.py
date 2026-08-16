from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

from agent.db_models import GoalDB
from agent.repository import goal_repo, task_repo
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service
from agent.services.goal_execution_contract_service import get_goal_execution_contract_service
from agent.services.goal_planning_recovery_service import get_goal_planning_recovery_service
from agent.services.request_cancellation_service import get_request_cancellation_service
from agent.services.task_queue_service import get_task_queue_service
from agent.services.task_runtime_service import update_local_task_status


def goal_mutation_lock_id(goal_id: str) -> str:
    """Canonical advisory-lock key for Goal/task materialization races."""

    return f"goal-task-materialization:{str(goal_id or '').strip()}"


@contextlib.contextmanager
def _mutation_locks(lock_port: Any, lock_ids: list[str]):
    multi = getattr(lock_port, "mutation_locks", None)
    if callable(multi):
        with multi(lock_ids) as acquired:
            yield bool(acquired)
        return
    with contextlib.ExitStack() as stack:
        for lock_id in sorted(set(lock_ids)):
            acquired = stack.enter_context(
                lock_port.mutation_lock(lock_id)
            )
            if not acquired:
                yield False
                return
        yield True


def _merge_rag_sources(
    goal_sources: dict,
    task_kind: str,
    *,
    include_global_defaults: bool = True,
) -> dict:
    try:
        from flask import current_app, has_app_context
        agent_cfg = (
            current_app.config.get("AGENT_CONFIG", {}) or {}
            if include_global_defaults and has_app_context()
            else {}
        )
    except Exception:
        agent_cfg = {}
    kc_cfg = dict((agent_cfg.get("knowledge_context") or {}).get("auto_include") or {})
    auto_kinds = [str(k).strip().lower() for k in list(kc_cfg.get("task_kinds") or []) if k]
    include_defaults = not auto_kinds or not task_kind or task_kind.lower() in auto_kinds

    def _ids(src: dict, key: str) -> list[str]:
        return [str(v).strip() for v in list(src.get(key) or []) if str(v).strip()]

    collection_ids = list(dict.fromkeys(
        _ids(goal_sources, "knowledge_collection_ids")
        + (_ids(kc_cfg, "knowledge_collection_ids") if include_defaults else [])
    ))
    artifact_ids = list(dict.fromkeys(
        _ids(goal_sources, "artifact_ids")
        + (_ids(kc_cfg, "artifact_ids") if include_defaults else [])
    ))
    repo_scope_refs = (
        list(goal_sources.get("repo_scope_refs") or [])
        + (list(kc_cfg.get("repo_scope_refs") or []) if include_defaults else [])
    )
    if not collection_ids and not artifact_ids and not repo_scope_refs:
        return {}
    return {
        "knowledge_collection_ids": collection_ids,
        "artifact_ids": artifact_ids,
        "repo_scope_refs": repo_scope_refs,
    }


class TaskLifecycleService:
    """Explicit task lifecycle use-cases to avoid scattered implicit status updates."""

    def materialize_from_plan_node(
        self,
        *,
        task_id: str,
        node: Any,
        team_id: str | None,
        goal_id: str | None,
        goal_trace_id: str | None,
        plan_id: str | None,
        parent_task_id: str | None,
        derivation_reason: str,
        derivation_depth: int,
        depends_on: list[str] | None,
        source_task_id: str | None = None,
        initial_status: str = "todo",
    ) -> None:
        rationale = dict(node.rationale or {})
        blueprint_provenance = {
            "blueprint_id": str(rationale.get("blueprint_id") or "").strip(),
            "blueprint_name": str(rationale.get("blueprint_name") or "").strip(),
            "blueprint_artifact_id": str(rationale.get("blueprint_artifact_id") or "").strip(),
            "blueprint_role_name": str(rationale.get("blueprint_role_name") or "").strip(),
            "template_name": str(rationale.get("template_name") or "").strip(),
            "template_id": str(rationale.get("template_id") or "").strip(),
        }
        blueprint_provenance = {
            key: value for key, value in blueprint_provenance.items() if value
        }
        shell_command_mode = str(rationale.get("shell_command_mode") or "").strip() or None
        task_kind = str(rationale.get("task_kind") or "").strip().lower()
        output_dir = ""
        goal_context_text = ""
        goal_rag_sources: dict = {}
        goal_mode_data: dict = {}
        goal_execution_contract: dict = {}
        goal_git_workspace_cfg: dict = {}
        goal_scope_key: str | None = None
        goal_workspace_sync_mode: str | None = None
        if goal_id:
            try:
                goal = goal_repo.get_by_id(str(goal_id))
                if goal:
                    output_dir = str((goal.execution_preferences or {}).get("output_dir") or "").strip()
                    goal_context_text = str(goal.goal or "").strip()
                    goal_rag_sources = dict((goal.execution_preferences or {}).get("rag_sources") or {})
                    goal_mode_data = dict(goal.mode_data or {})
                    goal_execution_contract = dict((goal.execution_preferences or {}).get("goal_execution_contract") or {})
                    _snap_cfg = dict(((goal.execution_preferences or {}).get("config_snapshot") or {}).get("config") or {})
                    _wr_cfg = dict((_snap_cfg.get("worker_runtime") or {}))
                    _reuse_mode = str(_wr_cfg.get("workspace_reuse_mode") or "").strip().lower()
                    if _reuse_mode == "goal_worker":
                        import re as _re
                        _raw_gid = str(goal_id).strip().lower()
                        _safe_gid = _re.sub(r"[^a-z0-9._-]+", "-", _raw_gid).strip("-.")
                        goal_scope_key = _safe_gid or None
                    _sync_mode = str(_wr_cfg.get("workspace_sync_mode") or "").strip().lower()
                    if _sync_mode:
                        goal_workspace_sync_mode = _sync_mode
            except Exception:
                pass
            try:
                from flask import current_app, has_app_context
                if (
                    derivation_reason != "goal_task_recovery"
                    and has_app_context()
                ):
                    agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
                    global_git_ws = dict((agent_cfg.get("workspace") or {}).get("git_workspace") or {})
                    if global_git_ws.get("enabled"):
                        bare_url = (
                            global_git_ws.get("remote_url")
                            or f"file:///project-workspaces/git-repos/{str(goal_id)[:12]}.git"
                        )
                        goal_git_workspace_cfg = {
                            "enabled": True,
                            "remote_url": bare_url,
                            "branch_strategy": global_git_ws.get("branch_strategy", "goal"),
                        }
            except Exception:
                pass
        research_context_input = _merge_rag_sources(
            goal_rag_sources,
            task_kind,
            include_global_defaults=(
                derivation_reason != "goal_task_recovery"
            ),
        )
        verification_spec = dict(node.verification_spec or {})

        deterministic_repair_foundation = goal_mode_data.get("deterministic_repair_foundation")
        if isinstance(deterministic_repair_foundation, dict) and deterministic_repair_foundation.get("repair_procedure"):
            extra_context = {"deterministic_repair_foundation": deterministic_repair_foundation}
        else:
            extra_context = {}

        worker_execution_context = {
            "kind": "worker_execution_context",
            "version": "v1",
            "planning_provenance": {
                "plan_id": plan_id,
                "plan_node_id": node.id,
                "goal_id": goal_id,
                "blueprint_role_defaults": dict((rationale or {}).get("blueprint_role_defaults") or {}),
                **blueprint_provenance,
            },
            "routing_hints": {
                "task_kind": rationale.get("task_kind"),
                "required_capabilities": list(rationale.get("required_capabilities") or []),
                "retrieval_intent": rationale.get("retrieval_intent"),
                "required_context_scope": rationale.get("required_context_scope"),
                "preferred_bundle_mode": rationale.get("preferred_bundle_mode"),
            },
            **({"context": {"context_text": goal_context_text}} if goal_context_text else {}),
            **({"workspace": {
                **({"output_dir": output_dir} if output_dir else {}),
                **({"git_workspace": goal_git_workspace_cfg} if goal_git_workspace_cfg else {}),
                **({"scope_key": goal_scope_key} if goal_scope_key else {}),
                **({"sync_mode": goal_workspace_sync_mode} if goal_workspace_sync_mode else {}),
            }} if (output_dir or goal_git_workspace_cfg or goal_scope_key or goal_workspace_sync_mode) else {}),
            **({"shell_command_mode": shell_command_mode} if shell_command_mode else {}),
            **({"research_context_input": research_context_input} if research_context_input else {}),
            **extra_context,
        }
        worker_execution_contract = get_goal_execution_contract_service().task_scoped_contract(
            goal_contract=goal_execution_contract,
            plan_id=plan_id,
            plan_node_id=node.id,
            expected_artifacts=list(verification_spec.get("expected_artifacts") or []),
        )
        worker_execution_context["worker_execution_contract"] = dict(
            worker_execution_contract or {}
        )
        worker_execution_context["expected_artifacts"] = list(
            verification_spec.get("expected_artifacts") or []
        )
        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )

        lock_ids = {str(task_id)}
        if goal_id:
            lock_ids.add(goal_mutation_lock_id(goal_id))
        if source_task_id or parent_task_id:
            lock_ids.add(
                str(source_task_id or parent_task_id)
            )
        with get_task_mutation_lock_port().mutation_locks(
            lock_ids
        ) as acquired:
            if not acquired:
                raise RuntimeError(
                    f"task_materialization_fence_unavailable:{task_id}"
                )
            if goal_id:
                authoritative_goal = goal_repo.get_by_id(
                    str(goal_id)
                )
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
                    in GoalLifecycleService._TERMINAL_GOAL_STATUSES
                ):
                    raise RuntimeError(
                        f"goal_terminal_task_materialization_denied:{goal_id}"
                    )
            get_task_queue_service().ingest_task(
                task_id=task_id,
                status=str(initial_status or "todo"),
                title=node.title,
                description=node.description,
                priority=node.priority,
                created_by="planning_service",
                source="goal_plan",
                team_id=team_id,
                event_type="task_materialized_from_plan",
                event_channel="planning_service",
                event_details={"plan_id": plan_id, "plan_node_id": node.id, "goal_id": goal_id},
                extra_fields={
                "goal_id": goal_id,
                "goal_trace_id": goal_trace_id,
                "plan_id": plan_id,
                # tasks.plan_node_id carries a foreign key to plan_nodes, and a
                # plan node is only persisted when its plan is. Pointing at a
                # node that was never written fails that key, so without a plan
                # the reference is left out; the same ids stay in
                # worker_execution_context for tracing, where nothing enforces
                # them.
                "plan_node_id": node.id if plan_id else None,
                "task_kind": rationale.get("task_kind"),
                "retrieval_intent": rationale.get("retrieval_intent"),
                "required_context_scope": rationale.get("required_context_scope"),
                "preferred_bundle_mode": rationale.get("preferred_bundle_mode"),
                "required_capabilities": list(rationale.get("required_capabilities") or []),
                "verification_spec": verification_spec,
                "expected_artifacts": list(verification_spec.get("expected_artifacts") or []),
                "worker_execution_context": worker_execution_context,
                "worker_execution_contract": worker_execution_contract,
                "status_reason_details": {
                    "materialized_from_plan": True,
                    "planning_provenance": {
                        "plan_id": plan_id,
                        "plan_node_id": node.id,
                        "blueprint_role_defaults": dict((rationale or {}).get("blueprint_role_defaults") or {}),
                        **blueprint_provenance,
                    },
                    "artifact_traceability": {
                        "plan_node_id": node.id,
                        "artifact_trace_id": str((rationale or {}).get("artifact_trace_id") or ""),
                        "expected_artifacts_count": len(list(verification_spec.get("expected_artifacts") or [])),
                    },
                },
                "parent_task_id": parent_task_id,
                "source_task_id": (
                    source_task_id
                    if source_task_id is not None
                    else parent_task_id
                ),
                "derivation_reason": derivation_reason,
                "derivation_depth": derivation_depth,
                "depends_on": depends_on if depends_on else None,
                },
            )

    def attach_verification_result(
        self,
        *,
        task_id: str,
        current_status: str,
        verification_spec: dict[str, Any],
        verification_status: dict[str, Any],
    ) -> None:
        update_local_task_status(
            task_id,
            current_status,
            verification_spec=verification_spec,
            verification_status=verification_status,
            event_type="task_verification_updated",
            event_actor="verification_service",
            event_details={
                "verification_status": verification_status.get("status"),
                "record_id": verification_status.get("record_id"),
            },
        )


class GoalLifecycleService:
    """Explicit goal lifecycle transitions with consistent metadata updates."""

    _TERMINAL_GOAL_STATUSES = {
        "completed",
        "failed",
        "cancelled",
        "aborted",
        "timeout",
        "archived",
    }
    _TERMINAL_TASK_STATUSES = {
        "completed",
        "failed",
        "cancelled",
        "verification_failed",
        "skipped",
        "aborted",
        "timeout",
        "archived",
    }

    @staticmethod
    def _recovery_source_ids(tasks: list[Any]) -> list[str]:
        source_ids: set[str] = set()
        for task in tasks:
            if (
                str(
                    getattr(task, "derivation_reason", "") or ""
                )
                == "goal_task_recovery"
            ):
                source_task_id = str(
                    getattr(task, "source_task_id", "") or ""
                ).strip()
                if source_task_id:
                    source_ids.add(source_task_id)
            recovery = dict(
                dict(
                    getattr(
                        task,
                        "status_reason_details",
                        None,
                    )
                    or {}
                ).get("model_recovery")
                or {}
            )
            if str(recovery.get("plan_id") or "").strip():
                task_id = str(
                    getattr(task, "id", "") or ""
                ).strip()
                if task_id:
                    source_ids.add(task_id)
        return sorted(source_ids)

    @classmethod
    def _recovery_lock_ids(
        cls,
        tasks: list[Any],
        *,
        goal_id: str | None = None,
    ) -> list[str]:
        # Lock every current Goal task.  Any of them can become a recovery
        # source while the terminal transition is racing.
        lock_ids = {
            str(getattr(task, "id", "") or "").strip()
            for task in tasks
            if str(getattr(task, "id", "") or "").strip()
        }
        lock_ids.update(cls._recovery_source_ids(tasks))
        for task in tasks:
            if (
                str(
                    getattr(task, "derivation_reason", "") or ""
                )
                == "goal_task_recovery"
            ):
                task_id = str(
                    getattr(task, "id", "") or ""
                ).strip()
                if task_id:
                    lock_ids.add(task_id)
        if goal_id:
            lock_ids.add(goal_mutation_lock_id(goal_id))
        return sorted(lock_ids)

    def _save_goal_recovery_status(self, goal: GoalDB, *, target_status: str, reason: str) -> GoalDB:
        goal.status = str(target_status)
        goal.updated_at = time.time()
        current = dict(goal.execution_preferences or {})
        current["last_status_reason"] = str(reason)
        try:
            scoped = get_goal_config_runtime_service().get_effective_config(
                goal_id=str(getattr(goal, "id", "") or "").strip() or None
            )
            current["goal_config_source"] = str(scoped.source or "global_fallback")
        except Exception:
            current["goal_config_source"] = "unavailable"
        goal.execution_preferences = current
        return goal_repo.save(goal)

    def transition_goal(
        self,
        goal: GoalDB,
        *,
        target_status: str,
        reason: str | None = None,
        readiness: dict[str, Any] | None = None,
    ) -> GoalDB:
        normalized_target = str(target_status or goal.status)
        goal_id = str(
            getattr(goal, "id", "") or ""
        ).strip()
        goal_tasks = (
            [
                task
                for task in task_repo.get_all()
                if str(
                    getattr(task, "goal_id", "") or ""
                ).strip()
                == goal_id
            ]
            if goal_id
            else []
        )
        terminal_transition = bool(
            normalized_target in self._TERMINAL_GOAL_STATUSES
            and goal_id
        )
        if not terminal_transition:
            return self._transition_goal_under_recovery_fence(
                goal,
                normalized_target=normalized_target,
                reason=reason,
                readiness=readiness,
                goal_tasks=goal_tasks,
            )

        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )

        lock_port = get_task_mutation_lock_port()
        lock_ids = self._recovery_lock_ids(
            goal_tasks,
            goal_id=goal_id,
        )
        saved_goal: GoalDB | None = None
        # A task that committed just before the Goal fence can be absent from
        # the initial snapshot.  Release and reacquire the complete canonical
        # set until the snapshot is stable; never acquire a newly discovered
        # child while holding only its lexically later source.
        for _attempt in range(8):
            retry_with_ids: list[str] | None = None
            with _mutation_locks(
                lock_port,
                lock_ids,
            ) as acquired:
                if not acquired:
                    raise RuntimeError(
                        "goal_recovery_fence_unavailable:" + goal_id
                    )
                fenced_goal_tasks = [
                    task
                    for task in task_repo.get_all()
                    if str(
                        getattr(task, "goal_id", "") or ""
                    ).strip()
                    == goal_id
                ]
                required_ids = self._recovery_lock_ids(
                    fenced_goal_tasks,
                    goal_id=goal_id,
                )
                if not set(required_ids).issubset(lock_ids):
                    retry_with_ids = sorted(
                        set(lock_ids).union(required_ids)
                    )
                else:
                    saved_goal = (
                        self._transition_goal_under_recovery_fence(
                            goal,
                            normalized_target=normalized_target,
                            reason=reason,
                            readiness=readiness,
                            goal_tasks=fenced_goal_tasks,
                        )
                    )
            if retry_with_ids is not None:
                lock_ids = retry_with_ids
                continue
            break
        if saved_goal is None:
            raise RuntimeError(
                "goal_recovery_fence_snapshot_unstable:" + goal_id
            )

        if terminal_transition:
            try:
                get_request_cancellation_service().cancel_goal_requests(
                    goal_id=goal_id,
                    include_workers=True,
                )
            except Exception:
                logging.exception(
                    "Goal %s Worker cancellation failed",
                    goal_id,
                )
        return saved_goal

    def _transition_goal_under_recovery_fence(
        self,
        goal: GoalDB,
        *,
        normalized_target: str,
        reason: str | None,
        readiness: dict[str, Any] | None,
        goal_tasks: list[Any],
    ) -> GoalDB:
        """Commit terminal Goal state before invalidating queued work."""

        goal.status = normalized_target
        goal.updated_at = time.time()
        if readiness is not None:
            goal.readiness = dict(readiness)
        if reason:
            current = dict(goal.execution_preferences or {})
            current["last_status_reason"] = str(reason)
            scoped = get_goal_config_runtime_service().get_effective_config(goal_id=str(getattr(goal, "id", "") or "").strip() or None)
            current["goal_config_source"] = str(scoped.source or "global_fallback")
            goal.execution_preferences = current
        saved_goal = goal_repo.save(goal)
        if (
            normalized_target in self._TERMINAL_GOAL_STATUSES
            and str(getattr(goal, "id", "") or "").strip()
        ):
            goal_id = str(goal.id)
            sweep_status = (
                "failed"
                if normalized_target in {"failed", "completed"}
                else "cancelled"
            )
            for task in goal_tasks:
                task_status = str(getattr(task, "status", "") or "").strip().lower()
                if task_status in self._TERMINAL_TASK_STATUSES:
                    continue
                try:
                    from agent.services.recovery_task_mutation_policy import (
                        recovery_task_role,
                    )

                    task_id = str(task.id)
                    event_details = {
                        "goal_id": goal_id,
                        "target_status": normalized_target,
                    }
                    if recovery_task_role(task) not in {
                        "source",
                        "child",
                    }:
                        update_local_task_status(
                            task_id,
                            sweep_status,
                            error=(
                                f"goal_terminal:{normalized_target}"
                            ),
                            event_type=(
                                "goal_terminal_task_sweep"
                            ),
                            event_actor=(
                                "goal_lifecycle_service"
                            ),
                            event_details=event_details,
                            force=True,
                        )
                        continue

                    invalidated_at = time.time()
                    reason_code = (
                        f"goal_terminal:{normalized_target}"
                    )
                    details = dict(
                        getattr(
                            task,
                            "status_reason_details",
                            None,
                        )
                        or {}
                    )
                    lease = details.get(
                        "recovery_dispatch_lease"
                    )
                    previous_lease = None
                    invalidated_lease = None
                    if isinstance(lease, dict) and str(
                        lease.get("state") or ""
                    ) in {"active", "worker_admitted"}:
                        previous_lease = dict(lease)
                        invalidated_lease = {
                            **lease,
                            "state": "revoked",
                            "revision": int(
                                lease.get("revision") or 0
                            )
                            + 1,
                            "revoked_at": invalidated_at,
                            "revocation_reason": reason_code,
                        }
                        details[
                            "recovery_dispatch_lease"
                        ] = invalidated_lease
                    marker = {
                        "schema": (
                            "ananta.recovery_owner_terminal_"
                            "invalidation.v1"
                        ),
                        "task_id": task_id,
                        "goal_id": goal_id,
                        "goal_status": normalized_target,
                        "previous_status": task_status,
                        "target_status": sweep_status,
                        "reason_code": reason_code,
                        "invalidated_at": invalidated_at,
                    }
                    details[
                        "recovery_owner_terminal_invalidation"
                    ] = marker
                    from agent.common.recovery_owner_terminal_write_boundary import (
                        authorize_recovery_owner_terminal_write,
                    )

                    with contextlib.ExitStack() as authority_stack:
                        authority_stack.enter_context(
                            authorize_recovery_owner_terminal_write(
                                task_id=task_id,
                                marker=marker,
                            )
                        )
                        if (
                            previous_lease is not None
                            and invalidated_lease is not None
                        ):
                            from agent.common.recovery_dispatch_invalidation_write_boundary import (
                                authorize_recovery_dispatch_invalidation_write,
                            )

                            authority_stack.enter_context(
                                authorize_recovery_dispatch_invalidation_write(
                                    task_id=task_id,
                                    current_lease=previous_lease,
                                    proposed_lease=invalidated_lease,
                                )
                            )
                        update_local_task_status(
                            task_id,
                            sweep_status,
                            status_reason_details=details,
                            error=reason_code,
                            event_type=(
                                "goal_terminal_task_sweep"
                            ),
                            event_actor=(
                                "goal_lifecycle_service"
                            ),
                            event_details=event_details,
                            force=True,
                        )
                except Exception:
                    logging.exception(
                        "Goal %s terminal sweep failed for task %s",
                        goal_id,
                        getattr(task, "id", ""),
                    )
        return saved_goal


    def recover_stalled_planning_goal(self, goal: GoalDB) -> GoalDB:
        """Re-triggers planning for a goal stuck in 'planning' with no tasks.

        Idempotent: capped at 2 attempts with a 60s cooldown.
        """
        status = str(getattr(goal, "status", "") or "").strip().lower()
        if status not in {"planning", "planning_queued", "planning_running"}:
            return goal
        goal_id = str(getattr(goal, "id", "") or "").strip()
        if not goal_id:
            return goal
        # Never trigger recovery re-planning while planning is actively queued/running.
        if status in {"planning_queued", "planning_running"}:
            return goal
        now_ts = time.time()
        updated_at = float(getattr(goal, "updated_at", 0.0) or 0.0)
        if updated_at and (now_ts - updated_at) < 30:
            return goal
        # If there is a very recent started planning run, do not trigger a second plan call.
        try:
            from agent.services.repository_registry import get_repository_registry
            planning_runs = [
                r
                for r in get_repository_registry().planning_run_repo.get_by_goal_id(goal_id, limit=20)
                if str(getattr(r, "goal_id", "") or "") == goal_id
            ]
            started_runs = [r for r in planning_runs if str(getattr(r, "status", "") or "").strip().lower() == "started"]
            if started_runs:
                latest_started = sorted(
                    started_runs,
                    key=lambda x: float(getattr(x, "updated_at", 0.0) or 0.0),
                    reverse=True,
                )[0]
                started_age = now_ts - float(getattr(latest_started, "updated_at", 0.0) or 0.0)
                if started_age <= 180:
                    return goal
        except Exception:
            pass
        tasks = [t for t in task_repo.get_all() if str(getattr(t, "goal_id", "") or "").strip() == goal_id]
        if tasks:
            return goal
        execution_preferences = dict(getattr(goal, "execution_preferences", None) or {})
        recovery = dict(execution_preferences.get("planning_recovery") or {})
        attempts = int(recovery.get("attempts") or 0)
        last_attempt_at = float(recovery.get("last_attempt_at") or 0.0)
        if attempts >= 2:
            return goal
        if last_attempt_at and (now_ts - last_attempt_at) < 60:
            return goal
        recovery.update({"attempts": attempts + 1, "last_attempt_at": now_ts, "last_reason": "stalled_planning_no_tasks"})
        execution_preferences["planning_recovery"] = recovery
        goal.execution_preferences = execution_preferences
        goal = goal_repo.save(goal)
        try:
            effective = dict(getattr(goal, "workflow_effective", None) or {})
            result = get_goal_planning_recovery_service().plan_goal(
                goal=str(getattr(goal, "goal", "") or ""),
                context=str(getattr(goal, "context", "") or "") or None,
                team_id=effective.get("routing", {}).get("team_id"),
                parent_task_id=None,
                create_tasks=bool(effective.get("planning", {}).get("create_tasks", True)),
                use_template=bool(effective.get("planning", {}).get("use_template", True)),
                use_repo_context=bool(effective.get("planning", {}).get("use_repo_context", True)),
                goal_id=goal.id,
                goal_trace_id=str(getattr(goal, "trace_id", "") or ""),
                mode=str(getattr(goal, "mode", "") or "generic"),
                mode_data=dict(getattr(goal, "mode_data", None) or {}),
            )
            if result.get("error"):
                recovery.update({"last_error": str(result.get("error"))[:240]})
                execution_preferences["planning_recovery"] = recovery
                goal.execution_preferences = execution_preferences
                goal = goal_repo.save(goal)
                if int(recovery.get("attempts") or 0) >= 2:
                    return self._save_goal_recovery_status(
                        goal,
                        target_status="failed",
                        reason=str(result.get("error") or "planning_failed"),
                    )
                return self._save_goal_recovery_status(
                    goal,
                    target_status="planning",
                    reason="planning_recovery_retry_scheduled",
                )
            created_task_ids = list(result.get("created_task_ids") or [])
            if not created_task_ids:
                recovery.update({"last_error": "planning_recovery_no_tasks_created"})
                execution_preferences["planning_recovery"] = recovery
                goal.execution_preferences = execution_preferences
                goal = goal_repo.save(goal)
                if int(recovery.get("attempts") or 0) >= 2:
                    return self._save_goal_recovery_status(
                        goal,
                        target_status="failed",
                        reason="planning_recovery_no_tasks_created",
                    )
                return self._save_goal_recovery_status(
                    goal,
                    target_status="planning",
                    reason="planning_recovery_retry_scheduled",
                )
            return self.transition_goal(goal, target_status="planned", reason="planning_recovery_completed")
        except Exception as exc:
            recovery.update({"last_error": str(exc)[:240]})
            execution_preferences["planning_recovery"] = recovery
            goal.execution_preferences = execution_preferences
            return goal_repo.save(goal)


task_lifecycle_service = TaskLifecycleService()
goal_lifecycle_service = GoalLifecycleService()


def get_task_lifecycle_service() -> TaskLifecycleService:
    return task_lifecycle_service


def get_goal_lifecycle_service() -> GoalLifecycleService:
    return goal_lifecycle_service
