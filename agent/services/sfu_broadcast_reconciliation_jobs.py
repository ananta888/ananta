"""Bounded scheduler adapters for Hub-owned Fleet and Route reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from agent.services.sfu_broadcast_reconciler_scheduler import SfuBroadcastJobContext
from agent.services.sfu_fanout_reconciliation_service import (
    ReconciliationRunStatus,
    RouteReconciliationCursor,
    RouteReconciliationScope,
    SfuFanoutRouteReconciliationService,
)
from agent.services.sfu_fleet_reconciliation_service import (
    SfuFleetReconciliationLease,
    SfuFleetReconciliationMutationPort,
    SfuFleetReconciliationPolicy,
    SfuFleetReconciliationService,
    SfuFleetReconciliationStatePort,
)


class SfuBroadcastReconciliationJobError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuRouteReconciliationScopeCandidate:
    scope: RouteReconciliationScope
    cursor_after: str


@dataclass(frozen=True, slots=True)
class SfuRouteReconciliationScopePage:
    items: tuple[SfuRouteReconciliationScopeCandidate, ...]
    next_cursor: str | None


class SfuRouteReconciliationScopePagePort(Protocol):
    def page(
        self, *, cursor: str | None, limit: int
    ) -> SfuRouteReconciliationScopePage: ...


class SfuRouteReconciliationCheckpointReaderPort(Protocol):
    def load_checkpoint(
        self, *, scope: RouteReconciliationScope
    ) -> RouteReconciliationCursor | None: ...


class _SchedulerFleetLeasePort:
    """Adapts one durable outer scheduler lease to the Fleet lease contract."""

    def __init__(self, context: SfuBroadcastJobContext) -> None:
        self._context = context
        self._lease: SfuFleetReconciliationLease | None = None

    def acquire(self, *, scope: str, owner_id: str, ttl_ms: int, now_ms: int):
        self._context.require_lease()
        outer = self._context.lease
        if owner_id != outer.owner_id or scope != f"sfu-fleet:{outer.partition_key}":
            raise SfuBroadcastReconciliationJobError("sfu_fleet_scheduler_scope_mismatch")
        self._lease = SfuFleetReconciliationLease(
            scope=scope,
            owner_id=owner_id,
            fencing_token=outer.fencing_token,
            checkpoint_cursor=outer.resume_cursor,
            expires_at_ms=int(outer.lease_expires_at * 1_000),
            version=outer.version,
        )
        return self._lease

    def renew(self, lease, *, ttl_ms: int, now_ms: int):
        if not self.is_current(lease, now_ms=now_ms):
            raise SfuBroadcastReconciliationJobError("sfu_background_job_lease_lost")
        return lease

    def is_current(self, lease, *, now_ms: int) -> bool:
        try:
            self._context.require_lease()
        except RuntimeError:
            return False
        return self._lease == lease and now_ms < lease.expires_at_ms

    def checkpoint(self, lease, *, cursor: str | None, now_ms: int):
        if not self.is_current(lease, now_ms=now_ms):
            raise SfuBroadcastReconciliationJobError("sfu_background_job_lease_lost")
        self._lease = replace(lease, checkpoint_cursor=cursor)
        return self._lease

    def release(self, lease, *, now_ms: int) -> None:
        if self._lease != lease:
            raise SfuBroadcastReconciliationJobError("sfu_background_job_lease_lost")


class SfuFleetReconciliationScheduledJob:
    def __init__(
        self,
        *,
        state: SfuFleetReconciliationStatePort,
        mutations: SfuFleetReconciliationMutationPort,
        policy: SfuFleetReconciliationPolicy | None = None,
    ) -> None:
        self._state = state
        self._mutations = mutations
        self._policy = policy

    def run(self, context: SfuBroadcastJobContext) -> str | None:
        context.require_lease()
        service = SfuFleetReconciliationService(
            leases=_SchedulerFleetLeasePort(context),
            state=self._state,
            mutations=self._mutations,
            policy=self._policy,
        )
        outcome = service.run_once(
            partition=context.lease.partition_key,
            owner_id=context.lease.owner_id,
        )
        if outcome.status in {"stopped", "skipped"}:
            raise SfuBroadcastReconciliationJobError(outcome.reason_code)
        return outcome.checkpoint_cursor


class SfuRouteReconciliationScheduledJob:
    def __init__(
        self,
        *,
        reconciler: SfuFanoutRouteReconciliationService,
        scopes: SfuRouteReconciliationScopePagePort,
        checkpoints: SfuRouteReconciliationCheckpointReaderPort,
    ) -> None:
        self._reconciler = reconciler
        self._scopes = scopes
        self._checkpoints = checkpoints

    def run(self, context: SfuBroadcastJobContext) -> str | None:
        context.require_lease()
        page = self._scopes.page(
            cursor=context.resume_cursor,
            limit=context.batch_size_max,
        )
        resume = context.resume_cursor
        for candidate in page.items:
            context.require_lease()
            result = self._reconciler.reconcile(
                scope=candidate.scope,
                owner_ref=context.lease.owner_id,
                cursor=self._checkpoints.load_checkpoint(scope=candidate.scope),
            )
            if result.status is ReconciliationRunStatus.FAILED:
                raise SfuBroadcastReconciliationJobError(result.reason_code)
            if result.status in {
                ReconciliationRunStatus.BUSY,
                ReconciliationRunStatus.PARTIAL,
            }:
                return resume
            resume = candidate.cursor_after
        return page.next_cursor


__all__ = [
    "SfuBroadcastReconciliationJobError",
    "SfuFleetReconciliationScheduledJob",
    "SfuRouteReconciliationCheckpointReaderPort",
    "SfuRouteReconciliationScheduledJob",
    "SfuRouteReconciliationScopeCandidate",
    "SfuRouteReconciliationScopePage",
    "SfuRouteReconciliationScopePagePort",
]
