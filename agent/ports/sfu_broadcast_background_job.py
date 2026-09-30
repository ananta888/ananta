"""Small Hub-side port for durable SFU background-job coordination."""

from __future__ import annotations

from typing import Protocol

from agent.models.sfu_broadcast_background_job import (
    SfuBroadcastBackgroundJobLease,
    SfuBroadcastBackgroundJobSpec,
)


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
    "SfuBroadcastBackgroundJobPort",
]
