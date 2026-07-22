"""Lease-fenced, bounded Hub maintenance job contracts and adapters."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from agent.services.sfu_broadcast_reconciler_scheduler import SfuBroadcastJobContext


@dataclass(frozen=True, slots=True)
class SfuMaintenanceBatchRequest:
    """Authority supplied by the durable Hub scheduler for one bounded batch."""

    partition_key: str
    owner_id: str
    fencing_token: int
    lease_expires_at: float
    resume_cursor: str | None
    batch_size_max: int
    runtime_deadline_ms: int
    now: float


@dataclass(frozen=True, slots=True)
class SfuMaintenanceBatchResult:
    processed: int
    next_cursor: str | None = None


class SfuCommandOutboxDeliveryPort(Protocol):
    """Durable command outbox delivery; the idempotency ledger is not this port."""

    def deliver_pending(
        self, request: SfuMaintenanceBatchRequest,
    ) -> SfuMaintenanceBatchResult: ...


class SfuDigestDestructionPendingPort(Protocol):
    """Idempotently destroy KMS material and CAS its durable metadata state."""

    def destroy_pending(
        self, request: SfuMaintenanceBatchRequest,
    ) -> SfuMaintenanceBatchResult: ...


class SfuBlindIndexReindexPort(Protocol):
    """Reindex one durable page using the configured active/previous key set."""

    def reindex_blind_indexes(
        self, request: SfuMaintenanceBatchRequest,
    ) -> SfuMaintenanceBatchResult: ...


class SfuTtlPurgePort(Protocol):
    """Physically purge one durable page after policy and legal-hold checks."""

    def purge_expired(
        self, request: SfuMaintenanceBatchRequest,
    ) -> SfuMaintenanceBatchResult: ...


class _SfuBoundedMaintenanceJob:
    _operation = ""

    def __init__(self, *, port: object, clock: Callable[[], float] = time.time) -> None:
        operation = getattr(port, self._operation, None)
        if not callable(operation):
            raise ValueError("sfu_maintenance_port_invalid")
        self._operation_call = operation
        self._clock = clock

    def run(self, context: SfuBroadcastJobContext) -> str | None:
        context.require_lease()
        now = float(self._clock())
        if not math.isfinite(now):
            raise RuntimeError("sfu_maintenance_clock_invalid")
        request = SfuMaintenanceBatchRequest(
            partition_key=context.lease.partition_key,
            owner_id=context.lease.owner_id,
            fencing_token=context.lease.fencing_token,
            lease_expires_at=context.lease.lease_expires_at,
            resume_cursor=context.resume_cursor,
            batch_size_max=context.batch_size_max,
            runtime_deadline_ms=context.lease.runtime_deadline_ms,
            now=now,
        )
        result = self._operation_call(request)
        context.require_lease()
        if (
            not isinstance(result, SfuMaintenanceBatchResult)
            or isinstance(result.processed, bool)
            or not 0 <= result.processed <= context.batch_size_max
            or (
                result.next_cursor is not None
                and (
                    not isinstance(result.next_cursor, str)
                    or not 1 <= len(result.next_cursor) <= 4096
                )
            )
        ):
            raise RuntimeError("sfu_maintenance_result_invalid")
        return result.next_cursor


class SfuCommandOutboxDeliveryJob(_SfuBoundedMaintenanceJob):
    _operation = "deliver_pending"


class SfuDigestDestructionPendingJob(_SfuBoundedMaintenanceJob):
    _operation = "destroy_pending"


class SfuBlindIndexReindexJob(_SfuBoundedMaintenanceJob):
    _operation = "reindex_blind_indexes"


class SfuTtlPurgeJob(_SfuBoundedMaintenanceJob):
    _operation = "purge_expired"


__all__ = [
    "SfuBlindIndexReindexJob",
    "SfuBlindIndexReindexPort",
    "SfuCommandOutboxDeliveryJob",
    "SfuCommandOutboxDeliveryPort",
    "SfuDigestDestructionPendingJob",
    "SfuDigestDestructionPendingPort",
    "SfuMaintenanceBatchRequest",
    "SfuMaintenanceBatchResult",
    "SfuTtlPurgeJob",
    "SfuTtlPurgePort",
]
