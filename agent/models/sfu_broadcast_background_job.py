"""Value types for durable SFU background-job coordination."""

from __future__ import annotations

from dataclasses import dataclass


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


__all__ = [
    "SfuBroadcastBackgroundJobLease",
    "SfuBroadcastBackgroundJobSpec",
]
