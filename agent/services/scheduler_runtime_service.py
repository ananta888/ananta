from __future__ import annotations

from agent.services.scheduler_service import get_scheduler_service


class SchedulerRuntimeService:
    """Endpoint-facing scheduler use-cases for add/list/remove operations."""

    def schedule_task(self, *, command: str, interval_seconds: int) -> dict:
        task = get_scheduler_service().get_instance().add_task(command, int(interval_seconds))
        return task.model_dump()

    def list_scheduled_tasks(self) -> list[dict]:
        return [task.model_dump() for task in get_scheduler_service().get_instance().tasks]

    def remove_scheduled_task(self, *, task_id: str) -> dict:
        get_scheduler_service().get_instance().remove_task(task_id)
        return {"status": "deleted"}

    def scheduler_state(self) -> dict:
        return get_scheduler_service().runtime_state()

    def start_scheduler(self) -> dict:
        get_scheduler_service().start()
        return self.scheduler_state()

    def stop_scheduler(self) -> dict:
        get_scheduler_service().stop()
        return self.scheduler_state()


scheduler_runtime_service = SchedulerRuntimeService()


def get_scheduler_runtime_service() -> SchedulerRuntimeService:
    return scheduler_runtime_service
