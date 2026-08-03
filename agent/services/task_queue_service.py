import time
from typing import Any, Callable, Dict, List, Optional

from agent.repository import goal_repo, task_repo
from agent.services.organization_task_dispatch_gate_service import (
    get_organization_task_dispatch_gate_service,
)
from agent.services.recovery_dispatch_gate_service import (
    get_recovery_dispatch_gate_service,
)
from agent.services.recovery_task_mutation_policy import (
    recovery_task_role,
)
from agent.services.task_runtime_service import (
    compare_and_set_local_task_status,
    update_local_task_status,
)
from agent.services.task_state_machine_service import can_autopilot_dispatch
from agent.services.task_status_service import normalize_task_status

_DEPENDENCY_SUCCESS_STATUSES = frozenset({"completed"})
_DEPENDENCY_FAILURE_TERMINAL_STATUSES = frozenset(
    {
        "failed",
        "verification_failed",
        "cancelled",
        "aborted",
        "timeout",
        "skipped",
        "archived",
    }
)


def _normalized_dependency_ids(
    task: Any,
    dependency_resolver: Callable[[Any], List[str]],
) -> List[str]:
    deps: List[str] = []
    seen: set[str] = set()
    task_id = str(getattr(task, "id", "") or "").strip()
    for dependency in list(dependency_resolver(task) or []):
        dependency_id = str(dependency or "").strip()
        if (
            not dependency_id
            or dependency_id == task_id
            or dependency_id in seen
        ):
            continue
        seen.add(dependency_id)
        deps.append(dependency_id)
    return deps


class TaskQueueService:
    """
    Read-/Statistik-Service fuer die aktuelle Dispatch-Queue.

    Der Service kapselt heute vor allem Queue-Sicht, Sortierung und Kennzahlen.
    Er ersetzt noch nicht die gesamte Orchestrierungs- und Mutationslogik.
    """

    def get_dispatch_queue(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Gibt die sortierte Liste der dispatch-bereiten Tasks zurueck."""
        from agent.routes.tasks.orchestration_policy.routing import build_dispatch_queue

        gate = get_recovery_dispatch_gate_service()
        organization_gate = get_organization_task_dispatch_gate_service()
        tasks = [
            task.model_dump()
            for task in task_repo.get_all()
            if gate.evaluate_task(task).allowed
            and organization_gate.evaluate(task).allowed
        ]
        queue = build_dispatch_queue(tasks)
        if limit:
            return queue[:limit]
        return queue

    def get_scoped_dispatch_queue(
        self,
        team_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        from agent.routes.tasks.orchestration_policy.routing import build_dispatch_queue

        now = float(now or time.time())
        tasks = task_repo.get_all()
        if team_id:
            tasks = [task for task in tasks if str(task.team_id or "") == str(team_id)]
        gate = get_recovery_dispatch_gate_service()
        organization_gate = get_organization_task_dispatch_gate_service()
        candidate_map = {
            task.id: task
            for task in tasks
            if can_autopilot_dispatch(
                task.status,
                manual_override_active=bool((getattr(task, "manual_override_until", None) or 0) > now),
            )
            and gate.evaluate_task(task).allowed
            and organization_gate.evaluate(task).allowed
        }
        queue = build_dispatch_queue([task.model_dump() for task in candidate_map.values()])
        return [
            {**item, "task": candidate_map.get(item["task_id"])}
            for item in queue
            if item["task_id"] in candidate_map
        ]

    def get_queue_stats(self) -> Dict[str, Any]:
        """Berechnet Statistiken ueber den aktuellen Zustand der Queue."""
        tasks = task_repo.get_all()
        stats = {
            "todo": 0,
            "assigned": 0,
            "in_progress": 0,
            "blocked": 0,
            "completed": 0,
            "failed": 0,
        }
        by_agent: Dict[str, int] = {}
        by_source: Dict[str, int] = {
            "ui": 0,
            "api": 0,
            "agent": 0,
            "system": 0,
            "unknown": 0,
        }

        for task_obj in tasks:
            task = task_obj.model_dump()
            status = normalize_task_status(task.get("status"), default="todo")
            if status in stats:
                stats[status] += 1

            agent = task.get("assigned_agent_url")
            if agent:
                by_agent[agent] = by_agent.get(agent, 0) + 1

            # Source-Ermittlung aus History
            history = task.get("history") or []
            source = "unknown"
            if history:
                first_ingest = next(
                    (h for h in history if isinstance(h, dict) and h.get("event_type") == "task_ingested"),
                    None,
                )
                source = str(((first_ingest or {}).get("details") or {}).get("source") or "unknown").lower()

            by_source[source if source in by_source else "unknown"] += 1

        return {
            "counts": stats,
            "by_agent": by_agent,
            "by_source": by_source,
            "depth": stats["todo"] + stats["assigned"] + stats.get("blocked", 0),
        }

    def ingest_task(
        self,
        *,
        task_id: str,
        status: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: str = "medium",
        created_by: str = "unknown",
        source: str = "ui",
        team_id: str | None = None,
        tags: list[str] | None = None,
        event_type: str = "task_ingested",
        event_channel: str = "central_task_management",
        event_details: dict[str, Any] | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        details = {"source": source, "channel": event_channel, "tags": list(tags or [])}
        if isinstance(event_details, dict):
            details.update(event_details)
        update_local_task_status(
            task_id,
            normalize_task_status(status, default="todo"),
            title=(str(title or "")[:200] or None),
            description=description,
            priority=priority,
            team_id=team_id,
            tags=list(tags or []),
            event_type=event_type,
            event_actor=created_by or "unknown",
            event_details=details,
            **dict(extra_fields or {}),
        )

    def lease_reserved_task(
        self,
        *,
        task_id: str,
        lease_owner: str,
        now: float,
        lease_until: float,
    ) -> bool:
        """Lease one stale reservation without making it dispatchable."""
        owner = str(lease_owner or "").strip()[:160]
        current_time = float(now)
        expiry = float(lease_until)
        if (
            not owner
            or expiry <= current_time
            or expiry > current_time + 300.0
        ):
            return False

        def available(task: Any) -> bool:
            details = dict(
                getattr(task, "status_reason_details", None) or {}
            )
            try:
                existing_expiry = float(
                    details.get(
                        "unsloth_cleanup_reservation_lease_until",
                        0.0,
                    )
                    or 0.0
                )
            except (TypeError, ValueError):
                return False
            return existing_expiry <= current_time

        return compare_and_set_local_task_status(
            task_id,
            "reserved",
            expected_statuses={"reserved"},
            authoritative_predicate=available,
            event_type="unsloth_cleanup_reservation_leased",
            event_actor="system:unsloth-control-plane",
            event_details={
                "admission_state": "reserved",
                "lease_owner": owner,
                "lease_until": expiry,
            },
            status_reason_details={
                "admission_state": "reserved",
                "unsloth_cleanup_reservation_lease_owner": owner,
                "unsloth_cleanup_reservation_lease_until": expiry,
            },
        )

    def activate_reserved_task(
        self,
        *,
        task_id: str,
        lease_owner: str | None = None,
    ) -> bool:
        """Make one admission-fenced reservation dispatchable."""
        owner = str(lease_owner or "").strip()

        def owns_lease(task: Any) -> bool:
            details = dict(
                getattr(task, "status_reason_details", None) or {}
            )
            return (
                not owner
                or details.get(
                    "unsloth_cleanup_reservation_lease_owner"
                )
                == owner
            )

        return compare_and_set_local_task_status(
            task_id,
            "created",
            expected_statuses={"reserved"},
            authoritative_predicate=owns_lease,
            event_type="unsloth_task_admission_activated",
            event_actor="system:unsloth-control-plane",
            event_details={
                "admission_state": "activated",
            },
        )

    def reject_reserved_task(
        self,
        *,
        task_id: str,
        reason_code: str,
        lease_owner: str | None = None,
    ) -> bool:
        """Terminally reject a reservation that failed its domain CAS."""
        reason = str(reason_code or "unsloth_task_admission_rejected")[
            :160
        ]
        owner = str(lease_owner or "").strip()

        def owns_lease(task: Any) -> bool:
            details = dict(
                getattr(task, "status_reason_details", None) or {}
            )
            return (
                not owner
                or details.get(
                    "unsloth_cleanup_reservation_lease_owner"
                )
                == owner
            )

        return compare_and_set_local_task_status(
            task_id,
            "cancelled",
            expected_statuses={"reserved"},
            authoritative_predicate=owns_lease,
            event_type="unsloth_task_admission_rejected",
            event_actor="system:unsloth-control-plane",
            event_details={
                "admission_state": "rejected",
                "reason_code": reason,
            },
            status_reason_code=reason,
            status_reason_details={
                "admission_state": "rejected",
            },
        )

    def claim_task(
        self,
        *,
        task_id: str,
        agent_url: str,
        lease_until: float,
        idempotency_key: str = "",
        claim_validator: Callable[
            [dict[str, Any]],
            tuple[bool, str | None],
        ]
        | None = None,
    ) -> bool:
        gate = get_recovery_dispatch_gate_service()
        initial = task_repo.get_by_id(task_id)
        if initial is None:
            return False
        from agent.services.lifecycle_service import (
            goal_mutation_lock_id,
        )
        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )

        goal_id = str(
            getattr(initial, "goal_id", "") or ""
        ).strip()
        source_task_id = str(
            getattr(initial, "source_task_id", "") or ""
        ).strip()
        lock_ids = {str(task_id)}
        if source_task_id:
            lock_ids.add(source_task_id)
        if goal_id:
            lock_ids.add(goal_mutation_lock_id(goal_id))
        with get_task_mutation_lock_port().mutation_locks(
            lock_ids
        ) as acquired:
            if not acquired:
                return False
            current = task_repo.get_by_id(task_id)
            if current is None:
                return False
            authoritative_goal_id = str(
                getattr(current, "goal_id", "") or ""
            ).strip()
            authoritative_source_id = str(
                getattr(current, "source_task_id", "") or ""
            ).strip()
            if (
                authoritative_goal_id != goal_id
                or authoritative_source_id != source_task_id
            ):
                return False
            if authoritative_goal_id:
                goal = goal_repo.get_by_id(
                    authoritative_goal_id
                )
                if (
                    goal is None
                    or str(getattr(goal, "status", "") or "")
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
                    return False
            gate_decision = gate.evaluate_task(current)
            if not gate_decision.allowed:
                return False
            organization_decision = (
                get_organization_task_dispatch_gate_service().evaluate(current)
            )
            if not organization_decision.allowed:
                return False
            current_status = normalize_task_status(
                getattr(current, "status", None),
                default="todo",
            )
            if current_status not in {
                "todo",
                "created",
                "assigned",
            }:
                return False
            if claim_validator is not None:
                allowed, _reason = claim_validator(
                    current.model_dump()
                )
                if not allowed:
                    return False
            elif current_status == "assigned":
                # Reclaim/renewal of an assigned task requires the Hub-owned
                # lease policy.  Bare callers cannot turn assigned -> assigned.
                return False
            if (
                current_status != "assigned"
                and not can_autopilot_dispatch(current_status)
            ):
                return False
            return compare_and_set_local_task_status(
                task_id,
                "assigned",
                expected_statuses={current_status},
                assigned_agent_url=agent_url,
                event_type="task_claimed",
                event_actor=agent_url,
                event_details={
                    "agent_url": agent_url,
                    "lease_until": lease_until,
                    "idempotency_key": idempotency_key,
                },
            )

    def reconcile_dependencies(
        self,
        *,
        tasks: List[Any],
        dependency_resolver: Callable[[Any], List[str]],
    ) -> List[Dict[str, Any]]:
        transitions: List[Dict[str, Any]] = []
        snapshot_by_id = {task.id: task for task in tasks}
        for task in tasks:
            live_task = task_repo.get_by_id(task.id) or snapshot_by_id.get(task.id)
            deps = _normalized_dependency_ids(
                live_task,
                dependency_resolver,
            )

            my_status = normalize_task_status(getattr(live_task, "status", None), default="todo")
            recovery_source = (
                recovery_task_role(live_task) == "source"
            )
            if recovery_source and my_status in {
                "completed",
                "verification_failed",
            }:
                from agent.services.recovery_source_post_commit_service import (
                    get_recovery_source_post_commit_service,
                )

                get_recovery_source_post_commit_service().deliver_if_pending(
                    live_task.id
                )
                continue
            if (
                recovery_source
                and my_status in {"blocked", "blocked_by_dependency"}
            ):
                from agent.services.recovery_source_finalization_service import (
                    get_recovery_source_finalization_service,
                )
                from agent.services.recovery_source_post_commit_service import (
                    get_recovery_source_post_commit_service,
                )

                finalization = (
                    get_recovery_source_finalization_service()
                    .finalize_if_ready(
                        source_task_id=live_task.id,
                        child_task_ids=deps,
                    )
                )
                if finalization.transitioned:
                    get_recovery_source_post_commit_service().deliver_if_pending(
                        live_task.id
                    )
                    transitions.append(
                        {
                            "task_id": live_task.id,
                            "event_type": "recovery_source_finalized",
                            "depends_on": deps,
                            "reason": finalization.reason_code,
                        }
                    )
                continue
            if not deps:
                if my_status in {"blocked", "blocked_by_dependency"}:
                    update_local_task_status(live_task.id, "todo")
                    transitions.append(
                        {
                            "task_id": live_task.id,
                            "event_type": "dependency_unblocked",
                            "depends_on": [],
                            "reason": "no_valid_dependencies",
                        }
                    )
                continue
            dep_statuses = []
            for dep_id in deps:
                dep_task = task_repo.get_by_id(dep_id) or snapshot_by_id.get(dep_id)
                if dep_task is None:
                    dep_statuses.append(("missing", dep_id))
                else:
                    dep_statuses.append((normalize_task_status(getattr(dep_task, "status", None), default=""), dep_id))
            recovery_child = (
                recovery_task_role(live_task) == "child"
            )
            has_failed = any(
                status in _DEPENDENCY_FAILURE_TERMINAL_STATUSES
                or (status == "missing" and recovery_child)
                for status, _ in dep_statuses
            )
            all_done = bool(dep_statuses) and all(
                status in _DEPENDENCY_SUCCESS_STATUSES
                for status, _ in dep_statuses
            )
            if my_status in {"blocked", "blocked_by_dependency"} and all_done:
                update_local_task_status(
                    live_task.id,
                    "todo",
                )
                transitions.append(
                    {
                        "task_id": live_task.id,
                        "event_type": "dependency_unblocked",
                        "depends_on": deps,
                        "reason": "all_dependencies_completed",
                    }
                )
            elif my_status in {"blocked", "blocked_by_dependency"} and has_failed:
                failed_dependency_ids = [
                    dep_id
                    for status, dep_id in dep_statuses
                    if (
                        status
                        in _DEPENDENCY_FAILURE_TERMINAL_STATUSES
                        or (status == "missing" and recovery_child)
                    )
                ]
                if recovery_child:
                    transitioned, failed_dependency_ids = (
                        self._fail_recovery_child_for_terminal_dependencies(
                            task_id=str(live_task.id),
                            source_task_id=str(
                                getattr(
                                    live_task,
                                    "source_task_id",
                                    "",
                                )
                                or ""
                            ),
                            dependency_ids=deps,
                            dependency_resolver=dependency_resolver,
                        )
                    )
                    if not transitioned:
                        continue
                    from agent.services.task_runtime_service import (
                        run_external_task_status_post_commit,
                    )

                    run_external_task_status_post_commit(
                        str(live_task.id),
                        old_status=my_status,
                        event_type="dependency_failed",
                        force=True,
                    )
                else:
                    update_local_task_status(
                        live_task.id,
                        "failed",
                        error=(
                            "dependency_failed:"
                            + ",".join(failed_dependency_ids)
                        ),
                        status_reason_code="dependency_terminal",
                    )
                transitions.append(
                    {
                        "task_id": live_task.id,
                        "event_type": "dependency_failed",
                        "depends_on": deps,
                        "reason": "dependency_failed",
                        "failed_dependency_ids": failed_dependency_ids,
                    }
                )
            elif my_status in {"todo", "created", "assigned"} and not all_done:
                update_local_task_status(live_task.id, "blocked_by_dependency")
                transitions.append(
                    {
                        "task_id": live_task.id,
                        "event_type": "dependency_blocked",
                        "depends_on": deps,
                        "reason": "waiting_for_dependencies",
                    }
                )
        return transitions

    @staticmethod
    def _fail_recovery_child_for_terminal_dependencies(
        *,
        task_id: str,
        source_task_id: str,
        dependency_ids: List[str],
        dependency_resolver: Callable[[Any], List[str]],
    ) -> tuple[bool, List[str]]:
        """Publish one dependency failure under a closed Hub capability."""

        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )

        normalized_task_id = str(task_id or "").strip()
        normalized_source_id = str(source_task_id or "").strip()
        lock_ids = {
            normalized_task_id,
            normalized_source_id,
            *dependency_ids,
        }
        lock_ids.discard("")
        for _attempt in range(8):
            retry_lock_ids: set[str] | None = None
            with get_task_mutation_lock_port().mutation_locks(
                lock_ids
            ) as acquired:
                if not acquired:
                    return False, []
                authoritative = task_repo.get_by_id(
                    normalized_task_id
                )
                if (
                    authoritative is None
                    or recovery_task_role(authoritative) != "child"
                    or normalize_task_status(
                        getattr(authoritative, "status", None),
                        default="todo",
                    )
                    not in {"blocked", "blocked_by_dependency"}
                ):
                    return False, []
                authoritative_source_id = str(
                    getattr(
                        authoritative,
                        "source_task_id",
                        "",
                    )
                    or ""
                ).strip()
                current_dependency_ids = (
                    _normalized_dependency_ids(
                        authoritative,
                        dependency_resolver,
                    )
                )
                required_lock_ids = {
                    normalized_task_id,
                    authoritative_source_id,
                    *current_dependency_ids,
                }
                required_lock_ids.discard("")
                if not required_lock_ids.issubset(lock_ids):
                    retry_lock_ids = lock_ids.union(
                        required_lock_ids
                    )
                else:
                    dependency_statuses: List[tuple[str, str]] = []
                    for dependency_id in current_dependency_ids:
                        dependency = task_repo.get_by_id(
                            dependency_id
                        )
                        dependency_statuses.append(
                            (
                                dependency_id,
                                (
                                    "missing"
                                    if dependency is None
                                    else normalize_task_status(
                                        getattr(
                                            dependency,
                                            "status",
                                            None,
                                        ),
                                        default="",
                                    )
                                ),
                            )
                        )
                    failed_dependency_ids = [
                        dependency_id
                        for dependency_id, status
                        in dependency_statuses
                        if status
                        in _DEPENDENCY_FAILURE_TERMINAL_STATUSES
                        or status == "missing"
                    ]
                    if not failed_dependency_ids:
                        return False, []
                    reconciled_at = time.time()
                    marker = {
                        "schema": (
                            "ananta.recovery_dependency_"
                            "reconciliation.v1"
                        ),
                        "task_id": normalized_task_id,
                        "source_task_id": authoritative_source_id,
                        "previous_status": normalize_task_status(
                            getattr(authoritative, "status", None),
                            default="todo",
                        ),
                        "target_status": "failed",
                        "reason_code": (
                            "recovery_dependency_terminal"
                        ),
                        "dependency_statuses": [
                            {
                                "task_id": dependency_id,
                                "status": status,
                            }
                            for dependency_id, status
                            in dependency_statuses
                        ],
                        "failed_dependency_ids": list(
                            failed_dependency_ids
                        ),
                        "reconciled_at": reconciled_at,
                    }
                    details = dict(
                        getattr(
                            authoritative,
                            "status_reason_details",
                            None,
                        )
                        or {}
                    )
                    details[
                        "recovery_dependency_reconciliation"
                    ] = marker
                    from agent.common.recovery_dependency_reconciliation_write_boundary import (
                        authorize_recovery_dependency_reconciliation_write,
                    )

                    with authorize_recovery_dependency_reconciliation_write(
                        task_id=normalized_task_id,
                        marker=marker,
                    ):
                        authoritative.status = "failed"
                        authoritative.status_reason_code = (
                            "recovery_dependency_terminal"
                        )
                        authoritative.status_reason_details = (
                            details
                        )
                        authoritative.updated_at = reconciled_at
                        persisted = task_repo.save(authoritative)
                    persisted_marker = dict(
                        dict(
                            getattr(
                                persisted,
                                "status_reason_details",
                                None,
                            )
                            or {}
                        ).get(
                            "recovery_dependency_reconciliation"
                        )
                        or {}
                    )
                    return (
                        bool(
                            persisted is not None
                            and normalize_task_status(
                                getattr(
                                    persisted,
                                    "status",
                                    None,
                                ),
                                default="",
                            )
                            == "failed"
                            and persisted_marker == marker
                        ),
                        list(failed_dependency_ids),
                    )
            if retry_lock_ids is None:
                return False, []
            lock_ids = retry_lock_ids
        raise RuntimeError(
            "recovery_dependency_reconciliation_snapshot_unstable:"
            + normalized_task_id
        )


def get_task_queue_service() -> TaskQueueService:
    return TaskQueueService()
