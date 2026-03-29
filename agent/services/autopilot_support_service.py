from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

from agent.config import settings
from agent.db_models import ConfigDB
from agent.services.repository_registry import get_repository_registry
from agent.services.task_runtime_service import update_local_task_status


class AutopilotSupportService:
    """Helper service for hub-owned autopilot trace, state, and dispatch support."""

    def append_trace_event(self, task_id: str, event_type: str, *, app=None, **data: Any) -> None:
        repos = get_repository_registry(app)
        task = repos.task_repo.get_by_id(task_id)
        if not task:
            return
        history = list(task.history or [])
        history.append({"event_type": event_type, "timestamp": time.time(), **data})
        update_local_task_status(task_id, task.status, history=history)

    def append_circuit_event_for_worker_tasks(self, worker_url: str, event_type: str, *, app=None, **data: Any) -> int:
        repos = get_repository_registry(app)
        affected = 0
        for task in repos.task_repo.get_all():
            if (task.assigned_agent_url or "") != worker_url:
                continue
            self.append_trace_event(task.id, event_type, app=app, worker_url=worker_url, **data)
            affected += 1
        return affected

    def persist_state(self, *, key: str, state: dict, app=None) -> None:
        repos = get_repository_registry(app)
        repos.config_repo.save(ConfigDB(key=key, value_json=json.dumps(state)))

    def load_state(self, *, key: str, app=None) -> dict | None:
        repos = get_repository_registry(app)
        cfg = repos.config_repo.get_by_key(key)
        if not cfg:
            return None
        try:
            return json.loads(cfg.value_json or "{}")
        except Exception:
            return None

    def scoped_tasks(self, *, team_id: str | None, app=None) -> list[Any]:
        repos = get_repository_registry(app)
        tasks = repos.task_repo.get_all()
        if team_id:
            tasks = [task for task in tasks if (task.team_id or "") == team_id]
        return list(tasks)

    def available_workers(
        self,
        *,
        team_id: str | None,
        is_worker_circuit_open,
        app_config: dict[str, Any],
        app=None,
    ) -> tuple[list[Any], int]:
        repos = get_repository_registry(app)
        workers = [a for a in repos.agent_repo.get_all() if (a.role or "").lower() == "worker" and a.status == "online"]
        workers_online_count = len(workers)
        if settings.role == "hub" and settings.hub_can_be_worker:
            my_url = (settings.agent_url or f"http://localhost:{settings.port}").rstrip("/")
            has_local = any((getattr(w, "url", "") or "").rstrip("/") == my_url for w in workers)
            if not has_local:
                workers.append(
                    SimpleNamespace(
                        url=my_url,
                        token=app_config.get("AGENT_TOKEN"),
                        status="online",
                        role="worker",
                        team_id=team_id,
                    )
                )
        workers = [worker for worker in workers if not is_worker_circuit_open(worker.url)]
        return workers, workers_online_count


autopilot_support_service = AutopilotSupportService()


def get_autopilot_support_service() -> AutopilotSupportService:
    return autopilot_support_service
