from __future__ import annotations

from agent.services.scheduler_service import get_scheduler_service
from agent.services.memory_tree_ingestion_service import get_memory_tree_ingestion_service
from agent.services.memory_tree_store_service import get_memory_tree_store_service


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

    def memory_ingestion_queue_status(self) -> dict:
        store = get_memory_tree_store_service()
        pending = len(store.list_jobs(status="pending"))
        leased = len(store.list_jobs(status="leased"))
        done = len(store.list_jobs(status="done"))
        failed = len(store.list_jobs(status="failed"))
        return {
            "pending": pending,
            "leased": leased,
            "done": done,
            "failed": failed,
            "total": pending + leased + done + failed,
        }

    def run_memory_ingestion_tick(self) -> dict:
        return get_memory_tree_ingestion_service().process_next_queue_job()


scheduler_runtime_service = SchedulerRuntimeService()


def get_scheduler_runtime_service() -> SchedulerRuntimeService:
    return scheduler_runtime_service
