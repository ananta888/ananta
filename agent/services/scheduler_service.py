from __future__ import annotations

from agent.scheduler import TaskScheduler, get_scheduler


class SchedulerService:
    """Small infrastructure seam around the scheduler singleton and its lifecycle."""

    def get_instance(self) -> TaskScheduler:
        return get_scheduler()

    def start(self) -> None:
        self.get_instance().start()

    def stop(self) -> None:
        self.get_instance().stop()

    def runtime_state(self) -> dict:
        scheduler = self.get_instance()
        return {
            "running": bool(scheduler.running),
            "thread_alive": bool(getattr(scheduler.thread, "is_alive", lambda: False)()),
            "scheduled_count": len(list(getattr(scheduler, "tasks", []) or [])),
            "running_task_ids": sorted(list(getattr(scheduler, "running_task_ids", set()) or set())),
        }


scheduler_service = SchedulerService()


def get_scheduler_service() -> SchedulerService:
    return scheduler_service
