from __future__ import annotations

import time
from typing import Any

from agent.common.audit import log_audit
from agent.db_models import archive_task_record, restore_task_record
from agent.services.recovery_task_mutation_policy import (
    recovery_task_role,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.task_archive_admin_mixin import (
    RecoveryChildAdminMutationConflict,
    TaskArchiveAdminMixin,
)
from agent.services.task_runtime_service import update_local_task_status
from agent.services.task_state_machine_service import can_transition, resolve_next_status
from agent.services.task_status_service import normalize_task_status
from agent.services.task_vector_admin_mixin import TaskVectorAdminMixin
from agent.services.vector_store_authorization_policy import (
    VectorAdminAuthorizationContext,
)


class _RetryTaskAdminFence(RuntimeError):
    pass


_TERMINAL_TASK_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "verification_failed",
        "skipped",
        "aborted",
        "timeout",
        "archived",
    }
)
_TERMINAL_GOAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "aborted",
        "timeout",
        "archived",
    }
)
_INFLIGHT_RECOVERY_LEASE_STATES = frozenset(
    {"active", "worker_admitted"}
)


class _RecoveryChildAdminMutationPolicy:
    """Pure Hub policy for independent Recovery-lineage administration."""

    _CLEANUP_ACTIONS = frozenset({"archive", "delete"})

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @classmethod
    def _conflict(
        cls,
        task: Any,
        *,
        action: str,
        reason_code: str,
    ) -> RecoveryChildAdminMutationConflict:
        details = cls._mapping(
            getattr(task, "status_reason_details", None)
        )
        source_recovery = cls._mapping(
            details.get("model_recovery")
        )
        source_strategy = cls._mapping(
            details.get("model_recovery_strategy")
        )
        task_id = str(getattr(task, "id", "") or "")
        return RecoveryChildAdminMutationConflict(
            reason_code=reason_code,
            task_id=task_id,
            source_task_id=(
                str(getattr(task, "source_task_id", "") or "").strip()
                or (
                    task_id
                    if (
                        str(
                            source_recovery.get("plan_id")
                            or ""
                        ).strip()
                        or source_strategy
                    )
                    else ""
                )
                or None
            ),
            plan_id=(
                str(getattr(task, "plan_id", "") or "").strip()
                or str(source_recovery.get("plan_id") or "").strip()
                or None
            ),
            action=action,
        )

    @classmethod
    def ensure_allowed(
        cls,
        task: Any,
        *,
        action: str,
        repos: Any,
        recovery_gate: Any,
        now: float | None = None,
        archived_target: bool = False,
    ) -> None:
        """Deny mutations that bypass the Hub-owned Recovery DAG.

        Cleanup is deliberately narrower than ordinary task cleanup.  A
        terminal child may be archived/deleted only after the source *and*
        Goal are terminal and the persisted Plan binding is still provable.
        At that point no dependency reconciliation or Worker result can make
        the lineage executable again.
        """

        task_details = cls._mapping(
            getattr(task, "status_reason_details", None)
        )
        source_recovery = cls._mapping(
            task_details.get("model_recovery")
        )
        source_strategy = cls._mapping(
            task_details.get("model_recovery_strategy")
        )
        recovery_role = recovery_task_role(task)
        is_recovery_child = bool(
            recovery_role == "child"
            or recovery_gate.is_recovery_child(task)
            or cls._mapping(
                task_details.get("recovery_dispatch_lease")
            )
        )
        is_recovery_source = bool(
            recovery_role == "source"
            or (
                not is_recovery_child
                and (
                    str(
                        source_recovery.get("plan_id") or ""
                    ).strip()
                    or source_strategy
                )
            )
        )
        if not (is_recovery_child or is_recovery_source):
            return

        normalized_action = str(action or "").strip().lower()
        task_status = normalize_task_status(
            getattr(task, "status", None),
            default="todo",
        )
        if normalized_action == "restore":
            raise cls._conflict(
                task,
                action=normalized_action,
                reason_code=(
                    "recovery_lineage_restore_requires_hub_control"
                ),
            )
        if normalized_action == "retention":
            raise cls._conflict(
                task,
                action=normalized_action,
                reason_code="recovery_lineage_retention_preserved",
            )
        if normalized_action == "retry":
            raise cls._conflict(
                task,
                action=normalized_action,
                reason_code=(
                    "recovery_child_retry_requires_new_hub_plan"
                    if is_recovery_child
                    else "recovery_source_retry_requires_new_hub_plan"
                ),
            )
        if (
            is_recovery_source
            and normalized_action == "delete"
        ):
            raise cls._conflict(
                task,
                action=normalized_action,
                reason_code=(
                    "recovery_source_cleanup_requires_hub_control"
                ),
            )
        if (
            is_recovery_source
            and normalized_action
            in {"pause", "resume", "cancel"}
        ):
            raise cls._conflict(
                task,
                action=normalized_action,
                reason_code=(
                    "recovery_source_cancel_requires_hub_control"
                    if normalized_action == "cancel"
                    else (
                        "recovery_source_mutation_requires_hub_control"
                    )
                ),
            )
        if not is_recovery_child:
            return
        if (
            normalized_action in {"pause", "resume", "cancel"}
            and task_status not in _TERMINAL_TASK_STATUSES
        ):
            raise cls._conflict(
                task,
                action=normalized_action,
                reason_code=(
                    "recovery_child_active_mutation_requires_hub_control"
                ),
            )
        if normalized_action not in cls._CLEANUP_ACTIONS:
            return

        source_task_id = str(
            getattr(task, "source_task_id", "") or ""
        ).strip()
        plan_id = str(
            getattr(task, "plan_id", "") or ""
        ).strip()
        goal_id = str(
            getattr(task, "goal_id", "") or ""
        ).strip()
        if not all((source_task_id, plan_id, goal_id)):
            raise cls._conflict(
                task,
                action=normalized_action,
                reason_code="recovery_child_binding_incomplete",
            )

        source = repos.task_repo.get_by_id(source_task_id)
        if source is None:
            source = repos.archived_task_repo.get_by_id(
                source_task_id
            )
        goal = repos.goal_repo.get_by_id(goal_id)
        plan = repos.plan_repo.get_by_id(plan_id)
        if source is None or goal is None or plan is None:
            raise cls._conflict(
                task,
                action=normalized_action,
                reason_code="recovery_child_owner_missing",
            )

        rationale = cls._mapping(
            getattr(plan, "rationale", None)
        )
        source_recovery = cls._mapping(
            cls._mapping(
                getattr(source, "status_reason_details", None)
            ).get("model_recovery")
        )
        binding_valid = bool(
            str(getattr(plan, "goal_id", "") or "") == goal_id
            and str(getattr(source, "goal_id", "") or "") == goal_id
            and str(rationale.get("source_task_id") or "")
            == source_task_id
            and str(source_recovery.get("plan_id") or "") == plan_id
            and str(getattr(plan, "status", "") or "")
            .strip()
            .lower()
            == "materialized"
        )
        if not binding_valid:
            raise cls._conflict(
                task,
                action=normalized_action,
                reason_code="recovery_child_binding_mismatch",
            )

        source_status = normalize_task_status(
            getattr(source, "status", None),
            default="",
        )
        goal_status = str(
            getattr(goal, "status", "") or ""
        ).strip().lower()
        lineage_closed = bool(
            task_status in _TERMINAL_TASK_STATUSES
            and source_status in _TERMINAL_TASK_STATUSES
            and goal_status in _TERMINAL_GOAL_STATUSES
        )
        if not lineage_closed:
            raise cls._conflict(
                task,
                action=normalized_action,
                reason_code=(
                    "recovery_child_cleanup_requires_closed_lineage"
                ),
            )

        lease = cls._mapping(
            cls._mapping(
                getattr(task, "status_reason_details", None)
            ).get("recovery_dispatch_lease")
        )
        lease_state = str(lease.get("state") or "").strip()
        try:
            lease_expires_at = float(
                lease.get("expires_at") or 0.0
            )
        except (TypeError, ValueError):
            # A malformed persisted in-flight lease is not evidence that the
            # capability expired.  Keep cleanup fail-closed.
            lease_expires_at = float("inf")
        if (
            lease_state in _INFLIGHT_RECOVERY_LEASE_STATES
            and lease_expires_at > float(now or time.time())
        ):
            raise cls._conflict(
                task,
                action=normalized_action,
                reason_code="recovery_child_dispatch_inflight",
            )


class TaskAdminService(
    TaskVectorAdminMixin,
    TaskArchiveAdminMixin,
):
    """Hub-owned task administration use-cases for archive, restore, hierarchy, and interventions."""

    @staticmethod
    def _task_admin_repositories():
        """Repository seam consumed by archive coordination mixins."""

        return get_repository_registry()

    def parse_status_filters(self, raw: object) -> set[str]:
        if raw is None:
            return set()
        if isinstance(raw, str):
            parts = [p.strip() for p in raw.split(",") if p.strip()]
        elif isinstance(raw, list):
            parts = [str(p).strip() for p in raw if str(p).strip()]
        else:
            parts = []
        return {normalize_task_status(p, default="") for p in parts if normalize_task_status(p, default="")}

    def task_matches_filters(
        self,
        task: dict,
        *,
        statuses: set[str],
        team_id: str,
        before_ts: float | None,
        task_ids: set[str],
    ) -> bool:
        if statuses and normalize_task_status(task.get("status"), default="") not in statuses:
            return False
        if team_id and (task.get("team_id") or "") != team_id:
            return False
        if before_ts is not None and float(task.get("updated_at") or task.get("created_at") or 0.0) >= before_ts:
            return False
        if task_ids and (task.get("id") or "") not in task_ids:
            return False
        return True

    def load_all_archived_tasks(self) -> list[dict]:
        repos = get_repository_registry()
        items: list[dict] = []
        limit = 500
        offset = 0
        while True:
            chunk = repos.archived_task_repo.get_all(limit=limit, offset=offset)
            if not chunk:
                break
            items.extend([item.model_dump() for item in chunk])
            if len(chunk) < limit:
                break
            offset += limit
        return items

    def build_task_tree(self, *, root_id: str, include_archived: bool, max_depth: int) -> dict | None:
        repos = get_repository_registry()
        active_items = [t.model_dump() for t in repos.task_repo.get_all()]
        archived_items = self.load_all_archived_tasks() if include_archived else []
        by_id: dict[str, dict] = {}
        children_by_parent: dict[str, list[str]] = {}

        for item in archived_items:
            item["_source"] = "archived"
            by_id[item["id"]] = item
        for item in active_items:
            item["_source"] = "active"
            by_id[item["id"]] = item

        for tid, item in by_id.items():
            parent_id = str(item.get("parent_task_id") or "").strip()
            if parent_id:
                children_by_parent.setdefault(parent_id, []).append(tid)

        if root_id not in by_id:
            return None

        def _node(task_id: str, depth: int, lineage: set[str]) -> dict:
            task = dict(by_id[task_id])
            child_ids = children_by_parent.get(task_id, [])
            out = {"task": task, "depth": depth, "children": [], "children_count": len(child_ids)}
            if depth >= max_depth:
                out["truncated"] = True
                return out
            for child_id in child_ids:
                if child_id in lineage:
                    out["children"].append({"task_id": child_id, "cycle_detected": True})
                    continue
                out["children"].append(_node(child_id, depth + 1, lineage | {child_id}))
            return out

        return _node(root_id, 0, {root_id})

    @staticmethod
    def _admin_mutation_lock_ids(task: Any) -> set[str]:
        from agent.services.lifecycle_service import (
            goal_mutation_lock_id,
        )

        task_id = str(getattr(task, "id", "") or "").strip()
        source_task_id = str(
            getattr(task, "source_task_id", "") or ""
        ).strip()
        goal_id = str(
            getattr(task, "goal_id", "") or ""
        ).strip()
        lock_ids = {task_id} if task_id else set()
        if source_task_id:
            lock_ids.add(source_task_id)
        if goal_id:
            lock_ids.add(goal_mutation_lock_id(goal_id))
        return lock_ids

    @staticmethod
    def _terminalize_active_task(
        task: Any,
        *,
        archive: bool,
    ) -> None:
        """Publish one removal transition through its narrow Hub authority."""

        task_id = str(getattr(task, "id", "") or "").strip()
        current_status = normalize_task_status(
            getattr(task, "status", None),
            default="todo",
        )
        if current_status in _TERMINAL_TASK_STATUSES:
            return
        if TaskAdminService._vector_index_task_marker(task):
            # The caller must cancel this domain through its lifecycle service
            # before entering the generic task mutation fence.  Retrying
            # outside the fence avoids a task-lock/lifecycle-lock inversion.
            raise _RetryTaskAdminFence()
        action = "archive" if archive else "delete"
        event_kwargs = {
            "force": True,
            "event_type": f"task_{action}d_terminalized",
            "event_actor": "task_admin_service",
            "event_details": {"archive": bool(archive)},
            "status_reason_code": f"task_{action}d",
        }
        details = dict(
            getattr(task, "status_reason_details", None) or {}
        )
        is_recovery_child = bool(
            str(
                getattr(task, "derivation_reason", "") or ""
            ).strip()
            == "goal_task_recovery"
            or details.get("model_recovery_release")
            or details.get("recovery_dispatch_lease")
        )
        is_recovery_source = bool(
            not is_recovery_child
            and (
                details.get("model_recovery")
                or details.get("model_recovery_strategy")
            )
        )
        if not (archive and is_recovery_source):
            update_local_task_status(
                task_id,
                "cancelled",
                **event_kwargs,
            )
            return

        from agent.common.recovery_task_admin_write_boundary import (
            authorize_recovery_task_admin_write,
        )

        with authorize_recovery_task_admin_write(
            task_id=task_id,
            source_task_id=task_id,
            goal_id=str(getattr(task, "goal_id", "") or ""),
            action=action,
            from_status=current_status,
            to_status="cancelled",
        ):
            update_local_task_status(
                task_id,
                "cancelled",
                **event_kwargs,
            )

    def _mutate_archived_task(
        self,
        *,
        task_id: str,
        action: str,
        vector_authorization: (
            VectorAdminAuthorizationContext | None
        ) = None,
    ) -> bool:
        """Restore or purge one archived row under its lineage fences."""

        from agent.services.recovery_dispatch_gate_service import (
            get_recovery_dispatch_gate_service,
        )
        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )

        repos = get_repository_registry()
        for _attempt in range(8):
            initial = repos.archived_task_repo.get_by_id(task_id)
            if initial is None:
                return False
            self._require_authorized_vector_index_task(
                initial,
                authorization=vector_authorization,
            )
            lock_ids = self._admin_mutation_lock_ids(initial)
            retry_fence = False
            with get_task_mutation_lock_port().mutation_locks(
                lock_ids
            ) as acquired:
                if not acquired:
                    raise RuntimeError(
                        "task_admin_archive_mutation_lock_unavailable:"
                        + task_id
                    )
                archived = repos.archived_task_repo.get_by_id(
                    task_id
                )
                if archived is None:
                    return False
                self._require_authorized_vector_index_task(
                    archived,
                    authorization=vector_authorization,
                )
                authoritative_lock_ids = (
                    self._admin_mutation_lock_ids(archived)
                )
                if not authoritative_lock_ids.issubset(lock_ids):
                    retry_fence = True
                else:
                    _RecoveryChildAdminMutationPolicy.ensure_allowed(
                        archived,
                        action=action,
                        repos=repos,
                        recovery_gate=(
                            get_recovery_dispatch_gate_service()
                        ),
                        archived_target=True,
                    )
                    if action == "restore":
                        task = restore_task_record(archived)
                        if task.status == "archived":
                            task.status = "todo"
                        repos.task_repo.save(task)
                    repos.archived_task_repo.delete(task_id)
                    return True
            if not retry_fence:
                break
        raise RuntimeError(
            "task_admin_archive_fence_snapshot_unstable:" + task_id
        )

    def _remove_active_task(
        self,
        *,
        task_id: str,
        archive: bool,
        vector_authorization: (
            VectorAdminAuthorizationContext | None
        ) = None,
        _fence_attempt: int = 0,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Terminalize, cascade, and cancel before archive/delete."""

        from agent.services.recovery_dispatch_gate_service import (
            get_recovery_dispatch_gate_service,
        )
        from agent.services.request_cancellation_service import (
            get_request_cancellation_service,
        )
        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )

        repos = get_repository_registry()
        initial = repos.task_repo.get_by_id(task_id)
        if initial is None:
            return False, None
        is_vector_index_task = (
            self._require_authorized_vector_index_task(
                initial,
                authorization=vector_authorization,
            )
        )
        if (
            is_vector_index_task
            and normalize_task_status(
                getattr(initial, "status", None),
                default="todo",
            )
            not in _TERMINAL_TASK_STATUSES
        ):
            from agent.services.vector_index_task_service import (
                get_vector_index_task_service,
            )

            get_vector_index_task_service().cancel(
                job_id=task_id,
                actor="task-admin-cleanup",
            )
            initial = repos.task_repo.get_by_id(task_id)
            if initial is None:
                return False, None
        lock_ids = self._admin_mutation_lock_ids(initial)
        initial_children = [
            child
            for child in list(repos.task_repo.get_all() or [])
            if str(
                getattr(child, "source_task_id", "") or ""
            ).strip()
            == str(task_id)
            and get_recovery_dispatch_gate_service().is_recovery_child(
                child
            )
        ]
        lock_ids.update(
            str(getattr(child, "id", "") or "")
            for child in initial_children
            if str(getattr(child, "id", "") or "")
        )
        cancellation_ids: set[str] = {str(task_id)}
        try:
            with get_task_mutation_lock_port().mutation_locks(
                lock_ids
            ) as acquired:
                if not acquired:
                    raise RuntimeError(
                        f"task_admin_mutation_lock_unavailable:{task_id}"
                    )
                task = repos.task_repo.get_by_id(task_id)
                if task is None:
                    return False, None
                self._require_authorized_vector_index_task(
                    task,
                    authorization=vector_authorization,
                )
                authoritative_lock_ids = (
                    self._admin_mutation_lock_ids(task)
                )
                children = [
                    child
                    for child in list(repos.task_repo.get_all() or [])
                    if str(
                        getattr(child, "source_task_id", "") or ""
                    ).strip()
                    == str(task_id)
                    and get_recovery_dispatch_gate_service().is_recovery_child(
                        child
                    )
                ]
                child_ids = {
                    str(getattr(child, "id", "") or "")
                    for child in children
                    if str(getattr(child, "id", "") or "")
                }
                required_ids = set(child_ids)
                required_ids.update(authoritative_lock_ids)
                if not required_ids.issubset(lock_ids):
                    raise _RetryTaskAdminFence()
                cancellation_ids.update(child_ids)
                _RecoveryChildAdminMutationPolicy.ensure_allowed(
                    task,
                    action="archive" if archive else "delete",
                    repos=repos,
                    recovery_gate=(
                        get_recovery_dispatch_gate_service()
                    ),
                )
                self._terminalize_active_task(
                    task,
                    archive=archive,
                )
                for child in children:
                    get_recovery_dispatch_gate_service().invalidate_task(
                        str(child.id),
                        reason_code="recovery_parent_removed",
                    )
                refreshed = repos.task_repo.get_by_id(task_id)
                if refreshed is None:
                    return False, None
                snapshot = refreshed.model_dump()
                if archive:
                    repos.archived_task_repo.save(archive_task_record(refreshed))
                repos.task_repo.delete(task_id)
        except _RetryTaskAdminFence:
            if _fence_attempt >= 7:
                raise RuntimeError(
                    f"task_admin_fence_snapshot_unstable:{task_id}"
                )
            return self._remove_active_task(
                task_id=task_id,
                archive=archive,
                vector_authorization=vector_authorization,
                _fence_attempt=_fence_attempt + 1,
            )

        cancellation = get_request_cancellation_service()
        for cancellation_id in sorted(
            value for value in cancellation_ids if value
        ):
            try:
                cancellation.cancel_task_requests(
                    task_id=cancellation_id,
                    include_workers=True,
                )
            except Exception:
                # The DB mutation already committed.  Cancellation fanout is
                # best effort and may be retried by the worker reconciler.
                import logging

                logging.exception(
                    "Task admin Worker cancellation failed for %s",
                    cancellation_id,
                )
        return True, snapshot

    def intervene_task(
        self,
        *,
        task_id: str,
        action: str,
        actor: str,
        vector_authorization: (
            VectorAdminAuthorizationContext | None
        ) = None,
    ) -> tuple[bool, str, dict]:
        from agent.services.recovery_dispatch_gate_service import (
            get_recovery_dispatch_gate_service,
        )
        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )

        repos = get_repository_registry()
        current = ""
        new_status = ""
        for fence_attempt in range(8):
            initial = repos.task_repo.get_by_id(task_id)
            if not initial:
                return False, "not_found", {}
            vector_intervention = (
                self._intervene_vector_index_task(
                    task=initial,
                    action=action,
                    actor=actor,
                    authorization=vector_authorization,
                )
            )
            if vector_intervention is not None:
                return vector_intervention
            lock_ids = self._admin_mutation_lock_ids(initial)
            retry_fence = False
            with get_task_mutation_lock_port().mutation_locks(
                lock_ids
            ) as acquired:
                if not acquired:
                    return False, (
                        "task_admin_mutation_lock_unavailable"
                    ), {"task_id": task_id}
                task = repos.task_repo.get_by_id(task_id)
                if not task:
                    return False, "not_found", {}
                authoritative_lock_ids = (
                    self._admin_mutation_lock_ids(task)
                )
                if not authoritative_lock_ids.issubset(lock_ids):
                    retry_fence = True
                elif self._vector_index_task_marker(task):
                    # Release the generic mutation fence and let the next
                    # iteration enter the dedicated lifecycle adapter.
                    retry_fence = True
                else:
                    try:
                        _RecoveryChildAdminMutationPolicy.ensure_allowed(
                            task,
                            action=action,
                            repos=repos,
                            recovery_gate=(
                                get_recovery_dispatch_gate_service()
                            ),
                        )
                    except RecoveryChildAdminMutationConflict as exc:
                        return (
                            False,
                            exc.reason_code,
                            exc.as_data(),
                        )

                    current = normalize_task_status(
                        task.status,
                        default="",
                    )
                    ok, reason = can_transition(action, current)
                    if not ok:
                        return False, reason, {
                            "current_status": current
                        }
                    new_status = resolve_next_status(
                        action,
                        current,
                        assigned_agent_url=task.assigned_agent_url,
                    )
                    update_kwargs: dict = {}
                    if action == "retry":
                        update_kwargs["last_exit_code"] = None
                    update_local_task_status(
                        task_id,
                        new_status,
                        event_type="task_intervention",
                        event_actor=actor,
                        event_details={
                            "action": action,
                            "previous_status": current,
                            "new_status": new_status,
                        },
                        manual_override_until=time.time() + 600,
                        **update_kwargs,
                    )
                    confirmed = repos.task_repo.get_by_id(task_id)
                    confirmed_status = normalize_task_status(
                        getattr(confirmed, "status", None),
                        default="",
                    )
                    if confirmed_status != new_status:
                        return False, (
                            "task_admin_status_commit_conflict"
                        ), {
                            "reason_code": (
                                "task_admin_status_commit_conflict"
                            ),
                            "task_id": task_id,
                            "source_task_id": (
                                str(
                                    getattr(
                                        task,
                                        "source_task_id",
                                        "",
                                    )
                                    or ""
                                ).strip()
                                or None
                            ),
                            "plan_id": (
                                str(
                                    getattr(task, "plan_id", "")
                                    or ""
                                ).strip()
                                or None
                            ),
                            "action": action,
                            "expected_status": new_status,
                            "current_status": confirmed_status,
                            "http_status": 409,
                        }
            if retry_fence:
                continue
            break
        else:
            return False, "task_admin_fence_snapshot_unstable", {
                "task_id": task_id
            }

        worker_cancel_forward = None
        if action == "cancel":
            from agent.services.request_cancellation_service import (
                get_request_cancellation_service,
            )

            worker_cancel_forward = (
                get_request_cancellation_service()
                .cancel_task_requests(
                    task_id=task_id,
                    include_workers=True,
                )
            )
        log_audit(
            "task_intervention",
            {
                "task_id": task_id,
                "action": action,
                "actor": actor,
                "previous_status": current,
                "new_status": new_status,
                **({"worker_cancel_forward": worker_cancel_forward} if isinstance(worker_cancel_forward, dict) else {}),
            },
        )
        return True, "ok", {
            "id": task_id,
            "action": action,
            "status": new_status,
            **({"worker_cancel_forward": worker_cancel_forward} if isinstance(worker_cancel_forward, dict) else {}),
        }


task_admin_service = TaskAdminService()


def get_task_admin_service() -> TaskAdminService:
    return task_admin_service
