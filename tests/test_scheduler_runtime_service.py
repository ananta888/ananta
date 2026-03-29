from agent.services import scheduler_runtime_service as scheduler_runtime_module


def test_scheduler_runtime_service_exposes_scheduler_state(monkeypatch):
    class StubSchedulerService:
        @staticmethod
        def runtime_state():
            return {
                "running": True,
                "thread_alive": True,
                "scheduled_count": 2,
                "running_task_ids": ["a", "b"],
            }

    monkeypatch.setattr(scheduler_runtime_module, "get_scheduler_service", lambda: StubSchedulerService())

    state = scheduler_runtime_module.get_scheduler_runtime_service().scheduler_state()

    assert state["running"] is True
    assert state["scheduled_count"] == 2
