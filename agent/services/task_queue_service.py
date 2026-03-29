import time
from typing import Any, Callable, Dict, List, Optional

from agent.repository import task_repo
from agent.routes.tasks.orchestration_policy.routing import build_dispatch_queue
from agent.routes.tasks.state_machine import can_autopilot_dispatch
from agent.routes.tasks.status import normalize_task_status


class TaskQueueService:
    """
    Read-/Statistik-Service fuer die aktuelle Dispatch-Queue.

    Der Service kapselt heute vor allem Queue-Sicht, Sortierung und Kennzahlen.
    Er ersetzt noch nicht die gesamte Orchestrierungs- und Mutationslogik.
    """

    def get_dispatch_queue(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Gibt die sortierte Liste der dispatch-bereiten Tasks zurueck."""
        tasks = [t.model_dump() for t in task_repo.get_all()]
        queue = build_dispatch_queue(tasks)
        if limit:
            return queue[:limit]
        return queue

    def get_scoped_dispatch_queue(self, team_id: Optional[str] = None, now: Optional[float] = None) -> List[Dict[str, Any]]:
        now = float(now or time.time())
        tasks = task_repo.get_all()
        if team_id:
            tasks = [task for task in tasks if str(task.team_id or "") == str(team_id)]
        candidate_map = {
            task.id: task
            for task in tasks
            if can_autopilot_dispatch(
                task.status,
                manual_override_active=bool((getattr(task, "manual_override_until", None) or 0) > now),
            )
        }
        queue = build_dispatch_queue([task.model_dump() for task in candidate_map.values()])
        return [{**item, "task": candidate_map.get(item["task_id"])} for item in queue if item["task_id"] in candidate_map]

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
        by_source: Dict[str, int] = {"ui": 0, "agent": 0, "system": 0, "unknown": 0}

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
    ) -> None:
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(
            task_id,
            normalize_task_status(status, default="todo"),
            title=(str(title or "")[:200] or None),
            description=description,
            priority=priority,
            team_id=team_id,
            tags=list(tags or []),
            event_type=event_type,
            event_actor=created_by or "unknown",
            event_details={"source": source, "channel": event_channel, "tags": list(tags or [])},
        )

    def claim_task(self, *, task_id: str, agent_url: str, lease_until: float, idempotency_key: str = "") -> None:
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(
            task_id,
            "assigned",
            assigned_agent_url=agent_url,
            event_type="task_claimed",
            event_actor=agent_url,
            event_details={"agent_url": agent_url, "lease_until": lease_until, "idempotency_key": idempotency_key},
        )

    def reconcile_dependencies(
        self,
        *,
        tasks: List[Any],
        dependency_resolver: Callable[[Any], List[str]],
    ) -> List[Dict[str, Any]]:
        from agent.routes.tasks.utils import _update_local_task_status

        transitions: List[Dict[str, Any]] = []
        by_id = {task.id: task for task in tasks}
        for task in tasks:
            deps = dependency_resolver(task)
            if not deps:
                continue
            dep_statuses = []
            for dep_id in deps:
                dep_task = by_id.get(dep_id)
                if dep_task is None:
                    dep_statuses.append(("missing", dep_id))
                else:
                    dep_statuses.append((str(dep_task.status or "").lower(), dep_id))
            my_status = str(task.status or "").lower()
            has_failed = any(status == "failed" for status, _ in dep_statuses)
            all_done = bool(dep_statuses) and all(status == "completed" for status, _ in dep_statuses)
            if my_status == "blocked" and all_done:
                _update_local_task_status(task.id, "todo")
                transitions.append(
                    {"task_id": task.id, "event_type": "dependency_unblocked", "depends_on": deps, "reason": "all_dependencies_completed"}
                )
            elif my_status == "blocked" and has_failed:
                failed_dependency_ids = [dep_id for status, dep_id in dep_statuses if status == "failed"]
                _update_local_task_status(task.id, "failed", error=f"dependency_failed:{','.join(failed_dependency_ids)}")
                transitions.append(
                    {
                        "task_id": task.id,
                        "event_type": "dependency_failed",
                        "depends_on": deps,
                        "reason": "dependency_failed",
                        "failed_dependency_ids": failed_dependency_ids,
                    }
                )
            elif my_status in {"todo", "created", "assigned"} and not all_done:
                _update_local_task_status(task.id, "blocked")
                transitions.append(
                    {"task_id": task.id, "event_type": "dependency_blocked", "depends_on": deps, "reason": "waiting_for_dependencies"}
                )
        return transitions


def get_task_queue_service() -> TaskQueueService:
    return TaskQueueService()
