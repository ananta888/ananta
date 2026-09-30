"""Recovery-lineage administration policy of Hub task administration.

Split out of ``task_admin_service`` (SRP): the pure Hub policy that decides
whether an operator may archive, delete or intervene in a Recovery child or
its source while the Recovery lineage is still active, plus the retry fence
and terminal-status vocabularies shared with ``TaskAdminService``.
"""

from __future__ import annotations

import time
from typing import Any

from agent.services.recovery_task_mutation_policy import (
    recovery_task_role,
)
from agent.services.task_archive_admin_mixin import (
    RecoveryChildAdminMutationConflict,
)
from agent.services.task_status_service import normalize_task_status


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
