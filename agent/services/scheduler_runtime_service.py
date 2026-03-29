from __future__ import annotations

from agent.scheduler import get_scheduler


class SchedulerRuntimeService:
    """Endpoint-facing scheduler use-cases for add/list/remove operations."""

    def schedule_task(self, *, command: str, interval_seconds: int) -> dict:
        task = get_scheduler().add_task(command, int(interval_seconds))
        return task.model_dump()

    def list_scheduled_tasks(self) -> list[dict]:
        return [task.model_dump() for task in get_scheduler().tasks]

    def remove_scheduled_task(self, *, task_id: str) -> dict:
        get_scheduler().remove_task(task_id)
        return {"status": "deleted"}


scheduler_runtime_service = SchedulerRuntimeService()


def get_scheduler_runtime_service() -> SchedulerRuntimeService:
    return scheduler_runtime_service
