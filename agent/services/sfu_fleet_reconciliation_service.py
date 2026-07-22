"""Hub-owned, lease/fencing protected SFU fleet reconciliation.

The service owns no threads and starts no workers.  A Hub background-job
lifecycle calls ``run_once``; durable lease, cursor and desired-state access are
supplied through ports so multiple Hub instances converge on one authority.
"""

from __future__ import annotations

from agent.services.sfu_broadcast_control_observability import (
    SfuBroadcastControlObservationPort,
    control_observer_or_null,
    observed_control_path,
)

import time
from dataclasses import dataclass
from typing import Callable, Protocol


class SfuFleetReconciliationError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class SfuFleetReconciliationPolicy:
    items_per_run_max: int = 200
    page_size_max: int = 50
    runtime_ms_max: int = 2_000
    lease_ttl_ms: int = 5_000
    renew_before_ms: int | None = None
    retry_max: int = 2
    retry_backoff_ms: int = 25

    def __post_init__(self) -> None:
        if not 1 <= self.items_per_run_max <= 10_000:
            raise ValueError("sfu_reconcile_items_bound_invalid")
        if not 1 <= self.page_size_max <= min(500, self.items_per_run_max):
            raise ValueError("sfu_reconcile_page_bound_invalid")
        if not 50 <= self.runtime_ms_max <= 60_000:
            raise ValueError("sfu_reconcile_runtime_bound_invalid")
        if not self.runtime_ms_max < self.lease_ttl_ms <= 300_000:
            raise ValueError("sfu_reconcile_lease_bound_invalid")
        renew_before_ms = self.renew_before_ms
        if renew_before_ms is None:
            renew_before_ms = min(1_500, max(1, self.lease_ttl_ms // 3))
            object.__setattr__(self, "renew_before_ms", renew_before_ms)
        if (
            isinstance(renew_before_ms, bool)
            or not isinstance(renew_before_ms, int)
            or not 0 < renew_before_ms < self.lease_ttl_ms
        ):
            raise ValueError("sfu_reconcile_renew_bound_invalid")
        if not 0 <= self.retry_max <= 5 or not 0 <= self.retry_backoff_ms <= 1_000:
            raise ValueError("sfu_reconcile_retry_bound_invalid")


@dataclass(frozen=True, slots=True)
class SfuFleetReconciliationLease:
    scope: str
    owner_id: str
    fencing_token: int
    checkpoint_cursor: str | None
    expires_at_ms: int
    version: int


@dataclass(frozen=True, slots=True)
class SfuFleetReconciliationItem:
    item_id: str
    cursor_after: str
    expected_state_version: int
    desired_route: bool
    desired_route_version: int
    active_route: bool
    active_route_version: int
    route_intent_expires_at_ms: int
    reservation_active: bool
    reservation_orphaned: bool
    reservation_expires_at_ms: int
    observation_fresh_until_ms: int
    stale_access_expires_at_ms: int
    node_health: str
    admission_ready: bool
    control_plane_consistent: bool


@dataclass(frozen=True, slots=True)
class SfuFleetReconciliationPage:
    items: tuple[SfuFleetReconciliationItem, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SfuFleetReconciliationOutcome:
    status: str
    reason_code: str
    fencing_token: int | None
    examined: int
    mutations: int
    checkpoint_cursor: str | None


class SfuFleetReconciliationLeasePort(Protocol):
    def acquire(
        self, *, scope: str, owner_id: str, ttl_ms: int, now_ms: int
    ) -> SfuFleetReconciliationLease | None: ...

    def renew(
        self, lease: SfuFleetReconciliationLease, *, ttl_ms: int, now_ms: int
    ) -> SfuFleetReconciliationLease: ...

    def is_current(self, lease: SfuFleetReconciliationLease, *, now_ms: int) -> bool: ...

    def checkpoint(
        self, lease: SfuFleetReconciliationLease, *, cursor: str | None, now_ms: int
    ) -> SfuFleetReconciliationLease: ...

    def release(self, lease: SfuFleetReconciliationLease, *, now_ms: int) -> None: ...


class SfuFleetReconciliationStatePort(Protocol):
    def scan(
        self,
        *,
        partition: str,
        cursor: str | None,
        limit: int,
        now_ms: int,
    ) -> SfuFleetReconciliationPage: ...


class SfuFleetReconciliationMutationPort(Protocol):
    def fence_route(
        self, *, item: SfuFleetReconciliationItem, fencing_token: int, reason_code: str
    ) -> bool: ...

    def release_reservation(
        self, *, item: SfuFleetReconciliationItem, fencing_token: int, reason_code: str
    ) -> bool: ...

    def mark_node_unknown(
        self, *, item: SfuFleetReconciliationItem, fencing_token: int, reason_code: str
    ) -> bool: ...

    def reconcile_desired_route(
        self,
        *,
        item: SfuFleetReconciliationItem,
        fencing_token: int,
        access_expires_at_ms: int,
    ) -> bool: ...


class SfuFleetReconciliationService:
    """Executes a single bounded reconciliation slice under a durable lease."""

    def __init__(
        self,
        *,
        leases: SfuFleetReconciliationLeasePort,
        state: SfuFleetReconciliationStatePort,
        mutations: SfuFleetReconciliationMutationPort,
        policy: SfuFleetReconciliationPolicy | None = None,
        wall_clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        monotonic_ms: Callable[[], int] = lambda: time.monotonic_ns() // 1_000_000,
        sleeper: Callable[[float], None] = time.sleep,
        control_observer: SfuBroadcastControlObservationPort | None = None,
    ) -> None:
        self._leases = leases
        self._state = state
        self._mutations = mutations
        self._policy = policy or SfuFleetReconciliationPolicy()
        self._wall_clock_ms = wall_clock_ms
        self._monotonic_ms = monotonic_ms
        self._sleeper = sleeper
        self._control_observer = control_observer_or_null(control_observer)

    @observed_control_path("fleet_failover")
    def run_once(self, *, partition: str, owner_id: str) -> SfuFleetReconciliationOutcome:
        if not partition or len(partition) > 128 or not owner_id or len(owner_id) > 128:
            raise SfuFleetReconciliationError("sfu_reconcile_scope_invalid")
        started = self._monotonic_ms()
        deadline = started + self._policy.runtime_ms_max
        try:
            lease = self._leases.acquire(
                scope=f"sfu-fleet:{partition}",
                owner_id=owner_id,
                ttl_ms=self._policy.lease_ttl_ms,
                now_ms=self._wall_clock_ms(),
            )
        except Exception as exc:
            return self._outcome("stopped", "sfu_reconcile_lease_store_unavailable", None, 0, 0, None)
        if lease is None:
            return self._outcome("skipped", "sfu_reconcile_lease_held", None, 0, 0, None)

        examined = 0
        mutations = 0
        cursor = lease.checkpoint_cursor
        reason = "sfu_reconcile_slice_complete"
        status = "completed"
        try:
            while examined < self._policy.items_per_run_max and self._monotonic_ms() < deadline:
                lease = self._renew_if_needed(lease)
                if not self._lease_current(lease):
                    status, reason = "stopped", "sfu_reconcile_lease_lost"
                    break
                page_limit = min(
                    self._policy.page_size_max,
                    self._policy.items_per_run_max - examined,
                )
                try:
                    page = self._state.scan(
                        partition=partition,
                        cursor=cursor,
                        limit=page_limit,
                        now_ms=self._wall_clock_ms(),
                    )
                except Exception:
                    status, reason = "stopped", "sfu_reconcile_state_unavailable"
                    break
                if not page.items:
                    cursor = page.next_cursor
                    lease = self._checkpoint(lease, cursor)
                    break
                for item in page.items:
                    if examined >= self._policy.items_per_run_max or self._monotonic_ms() >= deadline:
                        status, reason = "partial", "sfu_reconcile_runtime_budget_exhausted"
                        break
                    if not self._lease_current(lease):
                        status, reason = "stopped", "sfu_reconcile_lease_lost"
                        break
                    try:
                        mutations += self._retry_item(item, lease, deadline)
                    except SfuFleetReconciliationError as exc:
                        status, reason = "stopped", exc.reason_code
                        break
                    examined += 1
                    cursor = item.cursor_after
                    lease = self._checkpoint(lease, cursor)
                if status in {"stopped", "partial"}:
                    break
                if page.next_cursor is None:
                    cursor = None
                    lease = self._checkpoint(lease, None)
                    break
            else:
                if examined >= self._policy.items_per_run_max:
                    status, reason = "partial", "sfu_reconcile_item_budget_exhausted"
                elif self._monotonic_ms() >= deadline:
                    status, reason = "partial", "sfu_reconcile_runtime_budget_exhausted"
        finally:
            try:
                self._leases.release(lease, now_ms=self._wall_clock_ms())
            except Exception:
                if status == "completed":
                    status, reason = "completed", "sfu_reconcile_release_unconfirmed"
        return self._outcome(status, reason, lease.fencing_token, examined, mutations, cursor)

    def _retry_item(
        self,
        item: SfuFleetReconciliationItem,
        lease: SfuFleetReconciliationLease,
        deadline_ms: int,
    ) -> int:
        for attempt in range(self._policy.retry_max + 1):
            try:
                return self._reconcile_item(item, lease)
            except SfuFleetReconciliationError as exc:
                if not exc.retryable or attempt >= self._policy.retry_max:
                    raise
                delay_ms = min(self._policy.retry_backoff_ms * (2**attempt), 1_000)
                if self._monotonic_ms() + delay_ms >= deadline_ms:
                    raise SfuFleetReconciliationError("sfu_reconcile_retry_budget_exhausted") from exc
                self._sleeper(delay_ms / 1_000)
        raise SfuFleetReconciliationError("sfu_reconcile_retry_budget_exhausted")

    def _reconcile_item(
        self, item: SfuFleetReconciliationItem, lease: SfuFleetReconciliationLease
    ) -> int:
        now = self._wall_clock_ms()
        if not self._lease_current(lease):
            raise SfuFleetReconciliationError("sfu_reconcile_lease_lost")
        access_expires_at = min(
            item.route_intent_expires_at_ms,
            item.reservation_expires_at_ms,
            item.stale_access_expires_at_ms,
        )
        observation_stale = now >= item.observation_fresh_until_ms
        route_stale = (
            item.active_route
            and (
                now >= access_expires_at
                or observation_stale
                or not item.desired_route
                or item.active_route_version != item.desired_route_version
                or not item.control_plane_consistent
            )
        )
        changed = 0
        if route_stale:
            changed += int(
                self._mutate(
                    lease,
                    lambda: self._mutations.fence_route(
                        item=item,
                        fencing_token=lease.fencing_token,
                        reason_code="sfu_reconcile_route_stale",
                    ),
                )
            )
        if item.reservation_active and (
            item.reservation_orphaned
            or not item.desired_route
            or now >= item.reservation_expires_at_ms
        ):
            changed += int(
                self._mutate(
                    lease,
                    lambda: self._mutations.release_reservation(
                        item=item,
                        fencing_token=lease.fencing_token,
                        reason_code="sfu_reconcile_reservation_orphaned",
                    ),
                )
            )
        if observation_stale and item.node_health != "unknown":
            changed += int(
                self._mutate(
                    lease,
                    lambda: self._mutations.mark_node_unknown(
                        item=item,
                        fencing_token=lease.fencing_token,
                        reason_code="sfu_reconcile_observation_stale",
                    ),
                )
            )

        may_activate = (
            item.desired_route
            and now < access_expires_at
            and not observation_stale
            and item.node_health == "healthy"
            and item.admission_ready
            and item.reservation_active
            and not item.reservation_orphaned
            and item.control_plane_consistent
        )
        route_matches = item.active_route and item.active_route_version == item.desired_route_version
        if may_activate and not route_matches:
            changed += int(
                self._mutate(
                    lease,
                    lambda: self._mutations.reconcile_desired_route(
                        item=item,
                        fencing_token=lease.fencing_token,
                        access_expires_at_ms=access_expires_at,
                    ),
                )
            )
        return changed

    def _mutate(self, lease: SfuFleetReconciliationLease, action: Callable[[], bool]) -> bool:
        if not self._lease_current(lease):
            raise SfuFleetReconciliationError("sfu_reconcile_lease_lost")
        try:
            return bool(action())
        except SfuFleetReconciliationError:
            raise
        except Exception as exc:
            raise SfuFleetReconciliationError(
                "sfu_reconcile_mutation_unavailable", retryable=True
            ) from exc

    def _renew_if_needed(
        self, lease: SfuFleetReconciliationLease
    ) -> SfuFleetReconciliationLease:
        now = self._wall_clock_ms()
        if lease.expires_at_ms - now > self._policy.renew_before_ms:
            return lease
        try:
            return self._leases.renew(lease, ttl_ms=self._policy.lease_ttl_ms, now_ms=now)
        except Exception as exc:
            raise SfuFleetReconciliationError("sfu_reconcile_lease_lost") from exc

    def _checkpoint(
        self, lease: SfuFleetReconciliationLease, cursor: str | None
    ) -> SfuFleetReconciliationLease:
        if not self._lease_current(lease):
            raise SfuFleetReconciliationError("sfu_reconcile_lease_lost")
        try:
            return self._leases.checkpoint(lease, cursor=cursor, now_ms=self._wall_clock_ms())
        except Exception as exc:
            raise SfuFleetReconciliationError("sfu_reconcile_checkpoint_failed") from exc

    def _lease_current(self, lease: SfuFleetReconciliationLease) -> bool:
        try:
            return self._leases.is_current(lease, now_ms=self._wall_clock_ms())
        except Exception:
            return False

    @staticmethod
    def _outcome(
        status: str,
        reason_code: str,
        fencing_token: int | None,
        examined: int,
        mutations: int,
        cursor: str | None,
    ) -> SfuFleetReconciliationOutcome:
        return SfuFleetReconciliationOutcome(
            status=status,
            reason_code=reason_code,
            fencing_token=fencing_token,
            examined=examined,
            mutations=mutations,
            checkpoint_cursor=cursor,
        )
