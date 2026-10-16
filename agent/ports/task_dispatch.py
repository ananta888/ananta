"""Mutable outcome DTO shared by Hub dispatch coordinators."""
from dataclasses import dataclass


@dataclass
class TaskDispatchResult:
    task_id: str
    dispatched: bool = False
    completed: bool = False
    failed: bool = False
    failure_type: str | None = None
