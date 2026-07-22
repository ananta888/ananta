"""Bounded, Hub-owned reconciliation for persisted SFU fanout routes."""

from __future__ import annotations

from agent.services.sfu_broadcast_control_observability import (
    SfuBroadcastControlObservationPort,
    control_observer_or_null,
    observed_control_path,
)

from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from agent.services.sfu_broadcast_route_port import (
    ApplyRouteCommandV1,
    ApplyRoutePortV1,
    ObserveRoutePortV1,
    ObserveRouteQueryV1,
    RevokeRouteCommandV1,
    RevokeRoutePortV1,
    RouteContractViolationV1,
    RouteKeyV1,
    RouteMutationResultV1,
    RouteObservationResultV1,
    RouteOutcomeV1,
    RoutePresenceV1,
    RouteProjectionV1,
    RouteReasonCodeV1,
    RouteVersionV1,
    UpdateRouteCommandV1,
    UpdateRoutePortV1,
)


class ReconciliationPhase(str, Enum):
    REVOKE = "revoke"
    ENSURE = "ensure"


class ReconciliationDesiredState(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    TOMBSTONED = "tombstoned"
    UNKNOWN = "unknown"


class ReconciliationAction(str, Enum):
    CONVERGED = "converged"
    APPLIED = "applied"
    UPDATED = "updated"
    REVOKED = "revoked"
    DEFERRED = "deferred"
    FAILED = "failed"


class ReconciliationRunStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    BUSY = "busy"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SfuFanoutReconciliationConfig:
    reconcile_items_max: int = 100
    reconcile_deadline_ms: int = 2_000
    page_size: int = 25
    page_reads_max: int = 64
    lease_ttl_ms: int = 5_000

    def __post_init__(self) -> None:
        values = (
            self.reconcile_items_max,
            self.reconcile_deadline_ms,
            self.page_size,
            self.page_reads_max,
            self.lease_ttl_ms,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("sfu_route_reconciliation_config_invalid")
        if self.lease_ttl_ms < self.reconcile_deadline_ms:
            raise ValueError("sfu_route_reconciliation_lease_too_short")


@dataclass(frozen=True, slots=True)
class RouteReconciliationScope:
    tenant_ref: str
    room_ref: str


@dataclass(frozen=True, slots=True)
class RouteReconciliationCursor:
    phase: ReconciliationPhase
    token: str | None = None


@dataclass(frozen=True, slots=True)
class RouteReconciliationLease:
    scope: RouteReconciliationScope
    owner_ref: str
    fencing_token: str
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class RouteReconciliationCandidate:
    candidate_ref: str
    key: RouteKeyV1
    phase: ReconciliationPhase
    resume_cursor: str | None


@dataclass(frozen=True, slots=True)
class RouteReconciliationPage:
    items: tuple[RouteReconciliationCandidate, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class RouteReconciliationAuthority:
    """Atomically revalidated Hub state after runtime observation."""

    candidate_ref: str
    key: RouteKeyV1
    desired_state: ReconciliationDesiredState
    desired: RouteProjectionV1 | None
    expected_version: RouteVersionV1 | None
    revoke_version: RouteVersionV1 | None
    operation_id: str
    lease_fencing_token: str
    authorized: bool
    parent_active: bool
    epochs_current: bool
    route_fencing_current: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class RouteReconciliationItemOutcome:
    candidate_ref: str
    key: RouteKeyV1
    action: ReconciliationAction
    reason_code: str
    retryable: bool
    mutation: RouteMutationResultV1 | None = None


@dataclass(frozen=True, slots=True)
class SfuFanoutReconciliationResult:
    status: ReconciliationRunStatus
    reason_code: str
    processed: int
    converged: int
    mutated: int
    deferred: int
    failed: int
    pages_read: int
    next_cursor: RouteReconciliationCursor | None


class RouteReconciliationClock(Protocol):
    def now_ms(self) -> int: ...


class RouteReconciliationLeasePort(Protocol):
    def acquire(
        self,
        *,
        scope: RouteReconciliationScope,
        owner_ref: str,
        now_ms: int,
        lease_ttl_ms: int,
    ) -> RouteReconciliationLease | None: ...

    def release(self, lease: RouteReconciliationLease) -> None: ...


class RouteReconciliationPagePort(Protocol):
    def page(
        self,
        *,
        scope: RouteReconciliationScope,
        phase: ReconciliationPhase,
        cursor: str | None,
        page_size: int,
        lease_fencing_token: str,
        now_ms: int,
    ) -> RouteReconciliationPage: ...


class RouteReconciliationAuthorityPort(Protocol):
    def resolve(
        self,
        *,
        candidate: RouteReconciliationCandidate,
        observation: RouteObservationResultV1,
        lease: RouteReconciliationLease,
        now_ms: int,
    ) -> RouteReconciliationAuthority: ...


class RouteReconciliationCheckpointPort(Protocol):
    def save(
        self,
        *,
        lease: RouteReconciliationLease,
        cursor: RouteReconciliationCursor | None,
    ) -> None: ...


class RouteReconciliationOutcomePort(Protocol):
    def record(
        self,
        *,
        lease: RouteReconciliationLease,
        outcome: RouteReconciliationItemOutcome,
    ) -> None: ...


class SfuFanoutRouteReconciliationService:
    """Converges routes without owning policy, persistence, or vendor details."""

    def __init__(
        self,
        *,
        config: SfuFanoutReconciliationConfig,
        clock: RouteReconciliationClock,
        leases: RouteReconciliationLeasePort,
        pages: RouteReconciliationPagePort,
        authority: RouteReconciliationAuthorityPort,
        checkpoints: RouteReconciliationCheckpointPort,
        outcomes: RouteReconciliationOutcomePort,
        apply_routes: ApplyRoutePortV1,
        update_routes: UpdateRoutePortV1,
        revoke_routes: RevokeRoutePortV1,
        observe_routes: ObserveRoutePortV1,
        control_observer: SfuBroadcastControlObservationPort | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._leases = leases
        self._pages = pages
        self._authority = authority
        self._checkpoints = checkpoints
        self._outcomes = outcomes
        self._apply_routes = apply_routes
        self._update_routes = update_routes
        self._revoke_routes = revoke_routes
        self._observe_routes = observe_routes
        self._control_observer = control_observer_or_null(control_observer)

    @observed_control_path("route_reconcile")
    def reconcile(
        self,
        *,
        scope: RouteReconciliationScope,
        owner_ref: str,
        cursor: RouteReconciliationCursor | None = None,
    ) -> SfuFanoutReconciliationResult:
        started_at_ms = self._now_ms()
        deadline_ms = started_at_ms + self._config.reconcile_deadline_ms
        try:
            lease = self._leases.acquire(
                scope=scope,
                owner_ref=owner_ref,
                now_ms=started_at_ms,
                lease_ttl_ms=self._config.lease_ttl_ms,
            )
        except Exception:
            return self._empty_result(
                ReconciliationRunStatus.FAILED,
                "reconcile_lease_dependency_failure",
                cursor,
            )
        if lease is None:
            return self._empty_result(
                ReconciliationRunStatus.BUSY, "reconcile_lease_busy", cursor
            )
        if lease.scope != scope or lease.expires_at_ms <= started_at_ms:
            return self._release_with_result(
                lease,
                self._empty_result(
                    ReconciliationRunStatus.FAILED,
                    "reconcile_lease_invalid",
                    cursor,
                ),
            )
        active_cursor = cursor or RouteReconciliationCursor(
            ReconciliationPhase.REVOKE, None
        )
        counters = {
            "processed": 0,
            "converged": 0,
            "mutated": 0,
            "deferred": 0,
            "failed": 0,
            "pages_read": 0,
        }
        result: SfuFanoutReconciliationResult
        try:
            result = self._run(
                lease, active_cursor, deadline_ms, counters
            )
        except Exception:
            result = self._result(
                ReconciliationRunStatus.FAILED,
                "reconcile_dependency_failure",
                counters,
                active_cursor,
            )
        return self._release_with_result(lease, result)

    def _run(
        self,
        lease: RouteReconciliationLease,
        cursor: RouteReconciliationCursor,
        deadline_ms: int,
        counters: dict[str, int],
    ) -> SfuFanoutReconciliationResult:
        while True:
            now_ms = self._now_ms()
            if (
                counters["processed"] >= self._config.reconcile_items_max
                or counters["pages_read"] >= self._config.page_reads_max
                or now_ms >= deadline_ms
                or now_ms >= lease.expires_at_ms
            ):
                self._checkpoints.save(lease=lease, cursor=cursor)
                return self._result(
                    ReconciliationRunStatus.PARTIAL,
                    "reconcile_budget_exhausted",
                    counters,
                    cursor,
                )
            remaining = self._config.reconcile_items_max - counters["processed"]
            page = self._pages.page(
                scope=lease.scope,
                phase=cursor.phase,
                cursor=cursor.token,
                page_size=min(self._config.page_size, remaining),
                lease_fencing_token=lease.fencing_token,
                now_ms=now_ms,
            )
            counters["pages_read"] += 1
            if not page.items:
                if page.next_cursor is not None:
                    if page.next_cursor == cursor.token:
                        return self._result(
                            ReconciliationRunStatus.FAILED,
                            "reconcile_cursor_stalled",
                            counters,
                            cursor,
                        )
                    cursor = replace(cursor, token=page.next_cursor)
                    self._checkpoints.save(lease=lease, cursor=cursor)
                    continue
                if cursor.phase is ReconciliationPhase.REVOKE:
                    cursor = RouteReconciliationCursor(
                        ReconciliationPhase.ENSURE, None
                    )
                    self._checkpoints.save(lease=lease, cursor=cursor)
                    continue
                self._checkpoints.save(lease=lease, cursor=None)
                return self._result(
                    ReconciliationRunStatus.COMPLETED,
                    "reconcile_completed",
                    counters,
                    None,
                )
            for candidate in page.items:
                if candidate.phase is not cursor.phase:
                    return self._result(
                        ReconciliationRunStatus.FAILED,
                        "reconcile_phase_violation",
                        counters,
                        cursor,
                    )
                if self._now_ms() >= min(deadline_ms, lease.expires_at_ms):
                    self._checkpoints.save(lease=lease, cursor=cursor)
                    return self._result(
                        ReconciliationRunStatus.PARTIAL,
                        "reconcile_deadline_reached",
                        counters,
                        cursor,
                    )
                observation = self._observe_routes.observe(
                    ObserveRouteQueryV1(candidate.key)
                )
                authority = self._authority.resolve(
                    candidate=candidate,
                    observation=observation,
                    lease=lease,
                    now_ms=self._now_ms(),
                )
                outcome = self._reconcile_candidate(
                    candidate, authority, observation, lease, self._now_ms()
                )
                self._outcomes.record(lease=lease, outcome=outcome)
                counters["processed"] += 1
                if outcome.action is ReconciliationAction.CONVERGED:
                    counters["converged"] += 1
                elif outcome.action in {
                    ReconciliationAction.APPLIED,
                    ReconciliationAction.UPDATED,
                    ReconciliationAction.REVOKED,
                }:
                    counters["mutated"] += 1
                elif outcome.action is ReconciliationAction.DEFERRED:
                    counters["deferred"] += 1
                else:
                    counters["failed"] += 1
                cursor = RouteReconciliationCursor(
                    cursor.phase, candidate.resume_cursor
                )
                self._checkpoints.save(lease=lease, cursor=cursor)
                if counters["processed"] >= self._config.reconcile_items_max:
                    return self._result(
                        ReconciliationRunStatus.PARTIAL,
                        "reconcile_item_limit_reached",
                        counters,
                        cursor,
                    )
            cursor = replace(cursor, token=page.next_cursor)
            self._checkpoints.save(lease=lease, cursor=cursor)
            if page.next_cursor is None:
                if cursor.phase is ReconciliationPhase.REVOKE:
                    cursor = RouteReconciliationCursor(
                        ReconciliationPhase.ENSURE, None
                    )
                    self._checkpoints.save(lease=lease, cursor=cursor)
                else:
                    self._checkpoints.save(lease=lease, cursor=None)
                    return self._result(
                        ReconciliationRunStatus.COMPLETED,
                        "reconcile_completed",
                        counters,
                        None,
                    )

    def _reconcile_candidate(
        self,
        candidate: RouteReconciliationCandidate,
        authority: RouteReconciliationAuthority,
        observation: RouteObservationResultV1,
        lease: RouteReconciliationLease,
        now_ms: int,
    ) -> RouteReconciliationItemOutcome:
        if (
            authority.candidate_ref != candidate.candidate_ref
            or authority.key != candidate.key
            or authority.lease_fencing_token != lease.fencing_token
            or not authority.authorized
            or not authority.route_fencing_current
        ):
            return self._item(
                candidate,
                ReconciliationAction.DEFERRED,
                "reconcile_authority_or_fencing_denied",
                True,
            )
        desired = authority.desired
        requires_revoke = (
            authority.desired_state is not ReconciliationDesiredState.ACTIVE
            or not authority.parent_active
            or not authority.epochs_current
            or desired is None
            or desired.key != candidate.key
            or now_ms >= desired.expires_at_ms
        )
        if requires_revoke:
            return self._revoke(candidate, authority, observation, now_ms)
        assert desired is not None
        if candidate.phase is ReconciliationPhase.REVOKE:
            return self._item(
                candidate,
                ReconciliationAction.CONVERGED,
                "reconcile_candidate_reclassified_active",
                False,
            )
        if observation.presence is RoutePresenceV1.UNKNOWN:
            return self._item(
                candidate,
                ReconciliationAction.DEFERRED,
                observation.reason_code.value,
                observation.retryable,
            )
        if observation.presence is RoutePresenceV1.ABSENT:
            mutation = self._apply_routes.apply(
                ApplyRouteCommandV1(authority.operation_id, desired)
            )
            return self._from_mutation(
                candidate, ReconciliationAction.APPLIED, mutation
            )
        observed = observation.projection
        if observed is None:
            return self._item(
                candidate,
                ReconciliationAction.DEFERRED,
                "reconcile_active_projection_missing",
                True,
            )
        if observed == desired:
            return self._item(
                candidate,
                ReconciliationAction.CONVERGED,
                "reconcile_projection_current",
                False,
            )
        if (
            authority.expected_version != observed.version
            or not _is_successor(desired.version, observed.version)
        ):
            return self._revoke(candidate, authority, observation, now_ms)
        try:
            command = UpdateRouteCommandV1(
                authority.operation_id, observed.version, desired
            )
        except RouteContractViolationV1 as exc:
            return self._item(
                candidate, ReconciliationAction.DEFERRED, exc.reason_code, False
            )
        mutation = self._update_routes.update(command)
        return self._from_mutation(
            candidate, ReconciliationAction.UPDATED, mutation
        )

    def _revoke(
        self,
        candidate: RouteReconciliationCandidate,
        authority: RouteReconciliationAuthority,
        observation: RouteObservationResultV1,
        now_ms: int,
    ) -> RouteReconciliationItemOutcome:
        if observation.presence is RoutePresenceV1.ABSENT:
            return self._item(
                candidate,
                ReconciliationAction.CONVERGED,
                "reconcile_route_absent",
                False,
            )
        expected = authority.expected_version
        if observation.projection is not None:
            if expected != observation.projection.version:
                return self._item(
                    candidate,
                    ReconciliationAction.DEFERRED,
                    "reconcile_revoke_version_changed",
                    True,
                )
            expected = observation.projection.version
        if expected is None or authority.revoke_version is None:
            return self._item(
                candidate,
                ReconciliationAction.DEFERRED,
                "reconcile_revoke_version_missing",
                True,
            )
        try:
            command = RevokeRouteCommandV1(
                authority.operation_id,
                candidate.key,
                expected,
                authority.revoke_version,
                now_ms,
            )
        except RouteContractViolationV1 as exc:
            return self._item(
                candidate, ReconciliationAction.DEFERRED, exc.reason_code, False
            )
        mutation = self._revoke_routes.revoke(command)
        return self._from_mutation(
            candidate, ReconciliationAction.REVOKED, mutation
        )

    @staticmethod
    def _from_mutation(
        candidate: RouteReconciliationCandidate,
        successful_action: ReconciliationAction,
        mutation: RouteMutationResultV1,
    ) -> RouteReconciliationItemOutcome:
        if mutation.outcome is RouteOutcomeV1.ACKNOWLEDGED:
            action = successful_action
        elif mutation.retryable or mutation.outcome is RouteOutcomeV1.UNKNOWN:
            action = ReconciliationAction.DEFERRED
        elif (
            successful_action is ReconciliationAction.REVOKED
            and mutation.reason_code is RouteReasonCodeV1.NOT_FOUND
        ):
            action = ReconciliationAction.CONVERGED
        else:
            action = ReconciliationAction.FAILED
        return RouteReconciliationItemOutcome(
            candidate_ref=candidate.candidate_ref,
            key=candidate.key,
            action=action,
            reason_code=mutation.reason_code.value,
            retryable=mutation.retryable,
            mutation=mutation,
        )

    @staticmethod
    def _item(
        candidate: RouteReconciliationCandidate,
        action: ReconciliationAction,
        reason_code: str,
        retryable: bool,
    ) -> RouteReconciliationItemOutcome:
        return RouteReconciliationItemOutcome(
            candidate.candidate_ref,
            candidate.key,
            action,
            reason_code,
            retryable,
        )

    def _now_ms(self) -> int:
        value = self._clock.now_ms()
        if type(value) is not int or value <= 0:
            raise ValueError("sfu_route_reconciliation_clock_invalid")
        return value

    def _release_with_result(
        self,
        lease: RouteReconciliationLease,
        result: SfuFanoutReconciliationResult,
    ) -> SfuFanoutReconciliationResult:
        try:
            self._leases.release(lease)
        except Exception:
            return replace(
                result,
                status=ReconciliationRunStatus.FAILED,
                reason_code="reconcile_lease_release_failure",
            )
        return result

    @staticmethod
    def _result(status, reason, counters, cursor):
        return SfuFanoutReconciliationResult(
            status=status,
            reason_code=reason,
            processed=counters["processed"],
            converged=counters["converged"],
            mutated=counters["mutated"],
            deferred=counters["deferred"],
            failed=counters["failed"],
            pages_read=counters["pages_read"],
            next_cursor=cursor,
        )

    @staticmethod
    def _empty_result(status, reason, cursor):
        return SfuFanoutReconciliationResult(
            status, reason, 0, 0, 0, 0, 0, 0, cursor
        )


def _is_successor(candidate: RouteVersionV1, predecessor: RouteVersionV1) -> bool:
    return (
        candidate.route_epoch > predecessor.route_epoch
        and candidate.projection_version > predecessor.projection_version
        and candidate.topology_epoch >= predecessor.topology_epoch
        and candidate.key_epoch >= predecessor.key_epoch
        and candidate.fencing_token != predecessor.fencing_token
    )


__all__ = [
    "ReconciliationAction",
    "ReconciliationDesiredState",
    "ReconciliationPhase",
    "ReconciliationRunStatus",
    "RouteReconciliationAuthority",
    "RouteReconciliationAuthorityPort",
    "RouteReconciliationCandidate",
    "RouteReconciliationCheckpointPort",
    "RouteReconciliationClock",
    "RouteReconciliationCursor",
    "RouteReconciliationItemOutcome",
    "RouteReconciliationLease",
    "RouteReconciliationLeasePort",
    "RouteReconciliationOutcomePort",
    "RouteReconciliationPage",
    "RouteReconciliationPagePort",
    "RouteReconciliationScope",
    "SfuFanoutReconciliationConfig",
    "SfuFanoutReconciliationResult",
    "SfuFanoutRouteReconciliationService",
]
