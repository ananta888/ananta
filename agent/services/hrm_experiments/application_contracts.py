"""Contracts of the Hub-owned HRM experiment application service.

Errors, principals, the execution binding and the ports the application
service depends on (task queue, execution binding resolution).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class HrmApplicationError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class HrmPrincipal:
    tenant_id: str
    subject: str

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.subject:
            raise ValueError("HRM principal requires tenant and subject")


@dataclass(frozen=True, slots=True)
class HrmExecutionBinding:
    task_id: str
    worker_job_id: str
    assignment_id: str
    dispatch_lease_id: str
    worker_url: str
    deadline_epoch_ms: int


class HrmTaskQueuePort(Protocol):
    def create_run_task(self, *, run_id: str, profile_id: str, subject: str) -> str: ...

    def cancel_run_task(self, task_id: str, *, reason_code: str) -> None: ...


class HrmExecutionBindingPort(Protocol):
    def resolve(
        self, *, task_id: str, worker_job_id: str, worker_url: str
    ) -> HrmExecutionBinding: ...


__all__ = [
    "HrmApplicationError",
    "HrmExecutionBinding",
    "HrmExecutionBindingPort",
    "HrmPrincipal",
    "HrmTaskQueuePort",
]
