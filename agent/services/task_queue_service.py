from typing import Any, Dict, List, Optional

from agent.repository import task_repo
from agent.routes.tasks.orchestration_policy.routing import build_dispatch_queue
from agent.routes.tasks.status import normalize_task_status


class TaskQueueService:
    """
    Zentrale Logik fuer die Task-Queue-Verwaltung.
    Extrahiert aus HubServer/Orchestration-Logik (SRP).
    """

    def get_dispatch_queue(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Gibt die sortierte Liste der dispatch-bereiten Tasks zurueck."""
        tasks = [t.model_dump() for t in task_repo.get_all()]
        queue = build_dispatch_queue(tasks)
        if limit:
            return queue[:limit]
        return queue

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


def get_task_queue_service() -> TaskQueueService:
    return TaskQueueService()
