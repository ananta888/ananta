"""Narrow ports for Hub-owned spreadsheet execution scheduling."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class SpreadsheetExecutionQueuePort(Protocol):
    def enqueue(self, assignment: Mapping[str, Any]) -> tuple[dict[str, Any], bool]: ...

    def bind_dispatch(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_job_id: str,
        slot_lease_id: str,
        worker_id: str,
        status: str,
        queue_position: int | None,
    ) -> dict[str, Any]: ...

    def get(self, *, tenant_id: str, job_id: str) -> dict[str, Any]: ...

    def fail_dispatch(
        self,
        *,
        tenant_id: str,
        job_id: str,
        reason_code: str,
    ) -> dict[str, Any]: ...

    def claim(
        self,
        *,
        worker_id: str,
        callback_jti: str,
        artifact_handle_jti: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None: ...

    def consume_artifact_handle(
        self,
        *,
        tenant_id: str,
        job_id: str,
        jti: str,
    ) -> dict[str, Any]: ...

    def complete(
        self,
        *,
        tenant_id: str,
        job_id: str,
        callback_jti: str,
        result: Mapping[str, Any],
        callback_payload_digest: str,
    ) -> dict[str, Any]: ...

    def get_assignment(self, *, tenant_id: str, job_id: str) -> dict[str, Any]: ...

    def fail_claim(self, *, tenant_id: str, job_id: str, reason_code: str) -> dict[str, Any]: ...

    def fail_execution(
        self,
        *,
        tenant_id: str,
        job_id: str,
        callback_jti: str,
        reason_code: str,
        callback_payload_digest: str,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SpreadsheetWorkerJobBinding:
    worker_job_id: str


class SpreadsheetWorkerJobLedgerPort(Protocol):
    def create(
        self,
        *,
        queue_job_id: str,
        proposal_id: str,
        assignment_digest: str,
        worker_id: str,
    ) -> SpreadsheetWorkerJobBinding: ...

    def bind_lease(
        self,
        *,
        worker_job_id: str,
        slot_lease_id: str,
        status: str,
        queue_position: int | None,
        reason_code: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SpreadsheetLeaseDecision:
    status: str
    reason_code: str
    slot_lease_id: str | None
    queue_position: int | None = None


class SpreadsheetWorkerLeasePort(Protocol):
    def acquire(
        self,
        *,
        worker_job_id: str,
        queue_job_id: str,
        worker_id: str,
        assignment_digest: str,
    ) -> SpreadsheetLeaseDecision: ...


class SpreadsheetWorkerLeaseControlPort(Protocol):
    def claim(self, job: Mapping[str, Any]) -> None: ...

    def require_live(self, job: Mapping[str, Any]) -> None: ...

    def finish(self, job: Mapping[str, Any], *, status: str) -> None: ...


__all__ = [
    "SpreadsheetExecutionQueuePort",
    "SpreadsheetLeaseDecision",
    "SpreadsheetWorkerJobBinding",
    "SpreadsheetWorkerJobLedgerPort",
    "SpreadsheetWorkerLeaseControlPort",
    "SpreadsheetWorkerLeasePort",
]
