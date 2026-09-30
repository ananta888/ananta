"""Value types and error of the Hub semantic-compute lease authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from agent.db_models import SemanticComputeLeaseDB


class SemanticLeaseRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class LeaseRequest:
    tenant_id: str
    owner_subject: str
    contract_id: str
    contract_digest: str
    session_id: str
    epoch: int
    task_type: str
    audience: str
    role: str
    executor_id: str
    sequence_start: int
    sequence_end: int
    resource_budget: Mapping[str, int]
    ttl_seconds: float
    deadline_at: float


@dataclass(frozen=True, slots=True)
class LeaseScheduleCommit:
    """One atomic Hub scheduling result, including its replay projection."""

    leases: tuple[SemanticComputeLeaseDB, ...]
    result_payload: Mapping[str, object]
    replayed: bool


__all__ = [
    "LeaseRequest",
    "LeaseScheduleCommit",
    "SemanticLeaseRepositoryError",
]
