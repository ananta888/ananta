from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class WorkerEngine(Protocol):
    def submit_task(self, task_id: str, payload: dict) -> Any: ...
