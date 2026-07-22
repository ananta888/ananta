"""Small Hub-side port for durable SFU background-job coordination."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SfuBroadcastBackgroundJobSpec:
    name: str
    partition_key: str = "default"
    enabled: bool = False
    interval_ms_min: int = 10_000
    batch_size_max: int = 100
    runtime_deadline_ms: int = 5_000
    retry_max: int = 3
    backoff_ms: int = 1_000
    jitter_ms: int = 250
    retention_seconds: int = 86_400
    lease_seconds: float = 15.0


@dataclass(frozen=True, slots=True)
class SfuBroadcastBackgroundJobLease:
    job_id: str
    name: str
    partition_key: str
    owner_id: str
    fencing_token: int
    version: int
    lease_expires_at: float
    resume_cursor: str | None
    batch_size_max: int
    runtime_deadline_ms: int


class SfuBroadcastBackgroundJobPort(Protocol):
    def claim(
        self, spec: SfuBroadcastBackgroundJobSpec, *, owner_id: str, now: float
    ) -> SfuBroadcastBackgroundJobLease | None: ...

    def lease_valid(
        self, lease: SfuBroadcastBackgroundJobLease, *, now: float
    ) -> bool: ...

    def finish(
        self,
        lease: SfuBroadcastBackgroundJobLease,
        *,
        status: str,
        reason_code: str,
        resume_cursor: str | None,
        now: float,
    ) -> None: ...

    def release_owner(self, owner_id: str, *, now: float) -> int: ...


__all__ = [
    "SfuBroadcastBackgroundJobLease",
    "SfuBroadcastBackgroundJobPort",
    "SfuBroadcastBackgroundJobSpec",
]
