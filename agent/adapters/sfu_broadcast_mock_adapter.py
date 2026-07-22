"""Deterministic contract double for the SFU broadcast route ports."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Iterable, Protocol

from agent.services.sfu_broadcast_route_port import (
    ApplyRouteCommandV1,
    ObserveRouteQueryV1,
    RevokeRouteCommandV1,
    RouteKeyV1,
    RouteMutationResultV1,
    RouteObservationResultV1,
    RouteOperationV1,
    RouteOutcomeV1,
    RoutePresenceV1,
    RouteProjectionV1,
    RouteReasonCodeV1,
    RouteVersionV1,
    UpdateRouteCommandV1,
)


class RouteClockV1(Protocol):
    def now_ms(self) -> int: ...


class RouteFaultV1(str, Enum):
    ACK = "ack"
    DUPLICATE = "duplicate"
    REORDER = "reorder"
    TIMEOUT = "timeout"
    STALE_FENCING = "stale_fencing"
    PARTIAL = "partial"
    NODE_LOSS = "node_loss"
    RECOVERY = "recovery"


class RouteFaultPlanV1(Protocol):
    def next_fault(
        self,
        *,
        operation: RouteOperationV1,
        operation_id: str,
        key: RouteKeyV1,
    ) -> RouteFaultV1: ...


class ScriptedRouteFaultPlanV1:
    """FIFO fault script; an exhausted plan deterministically acknowledges."""

    def __init__(self, faults: Iterable[RouteFaultV1] = ()) -> None:
        self._faults = deque(faults)

    def push(self, *faults: RouteFaultV1) -> None:
        self._faults.extend(faults)

    def next_fault(
        self,
        *,
        operation: RouteOperationV1,
        operation_id: str,
        key: RouteKeyV1,
    ) -> RouteFaultV1:
        del operation, operation_id, key
        return self._faults.popleft() if self._faults else RouteFaultV1.ACK


@dataclass(frozen=True, slots=True)
class _RecordedMutation:
    command: object
    result: RouteMutationResultV1


class DeterministicSfuBroadcastRouteMockAdapter:
    """Atomic in-memory implementation of all four independent v1 ports.

    Combining the ports in this test double is only construction convenience;
    production consumers receive the individual protocols.  Every persisted
    projection is the exact immutable object supplied by the Hub.
    """

    def __init__(self, *, clock: RouteClockV1, fault_plan: RouteFaultPlanV1) -> None:
        self._clock = clock
        self._fault_plan = fault_plan
        self._routes: dict[RouteKeyV1, RouteProjectionV1] = {}
        self._tombstones: dict[RouteKeyV1, RouteVersionV1] = {}
        self._recorded: dict[str, _RecordedMutation] = {}
        self._runtime_available = True
        self._lock = RLock()

    def apply(self, command: ApplyRouteCommandV1) -> RouteMutationResultV1:
        return self._dispatch_mutation(
            operation=RouteOperationV1.APPLY,
            command=command,
            key=command.desired.key,
            mutate=lambda now_ms: self._apply(command, now_ms),
        )

    def update(self, command: UpdateRouteCommandV1) -> RouteMutationResultV1:
        return self._dispatch_mutation(
            operation=RouteOperationV1.UPDATE,
            command=command,
            key=command.desired.key,
            mutate=lambda now_ms: self._update(command, now_ms),
        )

    def revoke(self, command: RevokeRouteCommandV1) -> RouteMutationResultV1:
        return self._dispatch_mutation(
            operation=RouteOperationV1.REVOKE,
            command=command,
            key=command.key,
            mutate=lambda now_ms: self._revoke(command, now_ms),
        )

    def observe(self, query: ObserveRouteQueryV1) -> RouteObservationResultV1:
        with self._lock:
            now_ms = self._now_ms()
            fault = self._fault_plan.next_fault(
                operation=RouteOperationV1.OBSERVE,
                operation_id="observe",
                key=query.key,
            )
            recovered = fault is RouteFaultV1.RECOVERY
            if fault is RouteFaultV1.NODE_LOSS:
                self._runtime_available = False
            elif recovered:
                self._runtime_available = True
            if not self._runtime_available:
                return self._unknown_observation(query.key, RouteReasonCodeV1.RUNTIME_UNAVAILABLE, now_ms)
            if fault is RouteFaultV1.TIMEOUT:
                return self._unknown_observation(query.key, RouteReasonCodeV1.TIMEOUT, now_ms)
            if fault is RouteFaultV1.REORDER:
                return self._unknown_observation(query.key, RouteReasonCodeV1.COMMAND_REORDERED, now_ms)
            if fault is RouteFaultV1.STALE_FENCING:
                return self._unknown_observation(query.key, RouteReasonCodeV1.STALE_FENCING, now_ms)
            if fault is RouteFaultV1.PARTIAL:
                return self._unknown_observation(query.key, RouteReasonCodeV1.PARTIAL_APPLY_ROLLED_BACK, now_ms)

            projection = self._routes.get(query.key)
            if projection is not None:
                return RouteObservationResultV1(
                    key=query.key,
                    presence=RoutePresenceV1.ACTIVE,
                    reason_code=(RouteReasonCodeV1.RUNTIME_RECOVERED if recovered else RouteReasonCodeV1.ACTIVE),
                    projection=projection,
                    tombstone_version=None,
                    observed_at_ms=now_ms,
                    retryable=False,
                )
            return RouteObservationResultV1(
                key=query.key,
                presence=RoutePresenceV1.ABSENT,
                reason_code=(RouteReasonCodeV1.RUNTIME_RECOVERED if recovered else RouteReasonCodeV1.ABSENT),
                projection=None,
                tombstone_version=self._tombstones.get(query.key),
                observed_at_ms=now_ms,
                retryable=False,
            )

    def _dispatch_mutation(self, *, operation, command, key, mutate):
        with self._lock:
            now_ms = self._now_ms()
            operation_id = command.operation_id
            previous = self._recorded.get(operation_id)
            if previous is not None:
                if previous.command != command:
                    return self._result(
                        operation,
                        operation_id,
                        key,
                        RouteOutcomeV1.REJECTED,
                        RouteReasonCodeV1.COMMAND_ID_CONFLICT,
                        previous.result.observed_version,
                        now_ms,
                    )
                return self._result(
                    operation,
                    operation_id,
                    key,
                    RouteOutcomeV1.ACKNOWLEDGED,
                    RouteReasonCodeV1.DUPLICATE_IDEMPOTENT,
                    previous.result.observed_version,
                    now_ms,
                )

            fault = self._fault_plan.next_fault(
                operation=operation,
                operation_id=operation_id,
                key=key,
            )
            recovered = fault is RouteFaultV1.RECOVERY
            if fault is RouteFaultV1.NODE_LOSS:
                self._runtime_available = False
            elif recovered:
                self._runtime_available = True
            if not self._runtime_available:
                return self._result(
                    operation,
                    operation_id,
                    key,
                    RouteOutcomeV1.REJECTED,
                    RouteReasonCodeV1.RUNTIME_UNAVAILABLE,
                    self._current_version(key),
                    now_ms,
                )
            injected_reason = {
                RouteFaultV1.REORDER: RouteReasonCodeV1.COMMAND_REORDERED,
                RouteFaultV1.STALE_FENCING: RouteReasonCodeV1.STALE_FENCING,
                RouteFaultV1.PARTIAL: RouteReasonCodeV1.PARTIAL_APPLY_ROLLED_BACK,
            }.get(fault)
            if injected_reason is not None:
                return self._result(
                    operation,
                    operation_id,
                    key,
                    RouteOutcomeV1.REJECTED,
                    injected_reason,
                    self._current_version(key),
                    now_ms,
                )

            committed = mutate(now_ms)
            if not committed.acknowledged:
                return committed
            self._recorded[operation_id] = _RecordedMutation(command=command, result=committed)
            if fault is RouteFaultV1.TIMEOUT:
                return self._result(
                    operation,
                    operation_id,
                    key,
                    RouteOutcomeV1.UNKNOWN,
                    RouteReasonCodeV1.TIMEOUT,
                    committed.observed_version,
                    now_ms,
                )
            if fault is RouteFaultV1.DUPLICATE:
                return self._result(
                    operation,
                    operation_id,
                    key,
                    RouteOutcomeV1.ACKNOWLEDGED,
                    RouteReasonCodeV1.DUPLICATE_IDEMPOTENT,
                    committed.observed_version,
                    now_ms,
                )
            if recovered:
                return self._result(
                    operation,
                    operation_id,
                    key,
                    RouteOutcomeV1.ACKNOWLEDGED,
                    RouteReasonCodeV1.RUNTIME_RECOVERED,
                    committed.observed_version,
                    now_ms,
                )
            return committed

    def _apply(self, command: ApplyRouteCommandV1, now_ms: int) -> RouteMutationResultV1:
        desired = command.desired
        validity_error = self._validity_error(desired, now_ms)
        if validity_error is not None:
            return self._rejection(RouteOperationV1.APPLY, command.operation_id, desired.key, validity_error, now_ms)
        if desired.key in self._routes:
            return self._rejection(
                RouteOperationV1.APPLY,
                command.operation_id,
                desired.key,
                RouteReasonCodeV1.ALREADY_EXISTS,
                now_ms,
            )
        tombstone = self._tombstones.get(desired.key)
        if tombstone is not None:
            stale = self._successor_error(desired.version, tombstone)
            if stale is not None:
                return self._rejection(RouteOperationV1.APPLY, command.operation_id, desired.key, stale, now_ms)
        self._routes[desired.key] = desired
        self._tombstones.pop(desired.key, None)
        return self._ack(RouteOperationV1.APPLY, command.operation_id, desired.key, desired.version, now_ms)

    def _update(self, command: UpdateRouteCommandV1, now_ms: int) -> RouteMutationResultV1:
        desired = command.desired
        current = self._routes.get(desired.key)
        if current is None:
            return self._rejection(
                RouteOperationV1.UPDATE,
                command.operation_id,
                desired.key,
                RouteReasonCodeV1.NOT_FOUND,
                now_ms,
            )
        mismatch = self._expected_version_error(command.expected_version, current.version)
        if mismatch is not None:
            return self._rejection(RouteOperationV1.UPDATE, command.operation_id, desired.key, mismatch, now_ms)
        validity_error = self._validity_error(desired, now_ms)
        if validity_error is not None:
            return self._rejection(RouteOperationV1.UPDATE, command.operation_id, desired.key, validity_error, now_ms)
        self._routes[desired.key] = desired
        return self._ack(RouteOperationV1.UPDATE, command.operation_id, desired.key, desired.version, now_ms)

    def _revoke(self, command: RevokeRouteCommandV1, now_ms: int) -> RouteMutationResultV1:
        current = self._routes.get(command.key)
        if current is None:
            return self._rejection(
                RouteOperationV1.REVOKE,
                command.operation_id,
                command.key,
                RouteReasonCodeV1.NOT_FOUND,
                now_ms,
            )
        mismatch = self._expected_version_error(command.expected_version, current.version)
        if mismatch is not None:
            return self._rejection(RouteOperationV1.REVOKE, command.operation_id, command.key, mismatch, now_ms)
        del self._routes[command.key]
        self._tombstones[command.key] = command.revoke_version
        return self._ack(
            RouteOperationV1.REVOKE,
            command.operation_id,
            command.key,
            command.revoke_version,
            now_ms,
        )

    @staticmethod
    def _validity_error(projection: RouteProjectionV1, now_ms: int) -> RouteReasonCodeV1 | None:
        if now_ms < projection.issued_at_ms:
            return RouteReasonCodeV1.NOT_YET_VALID
        if now_ms >= projection.expires_at_ms:
            return RouteReasonCodeV1.EXPIRED
        return None

    @staticmethod
    def _expected_version_error(expected: RouteVersionV1, actual: RouteVersionV1) -> RouteReasonCodeV1 | None:
        if expected.route_epoch != actual.route_epoch:
            return (
                RouteReasonCodeV1.STALE_ROUTE_EPOCH
                if expected.route_epoch < actual.route_epoch
                else RouteReasonCodeV1.VERSION_CONFLICT
            )
        if expected.topology_epoch != actual.topology_epoch:
            return (
                RouteReasonCodeV1.STALE_TOPOLOGY_EPOCH
                if expected.topology_epoch < actual.topology_epoch
                else RouteReasonCodeV1.VERSION_CONFLICT
            )
        if expected.key_epoch != actual.key_epoch:
            return (
                RouteReasonCodeV1.STALE_KEY_EPOCH
                if expected.key_epoch < actual.key_epoch
                else RouteReasonCodeV1.VERSION_CONFLICT
            )
        if expected.projection_version != actual.projection_version:
            return (
                RouteReasonCodeV1.STALE_PROJECTION_VERSION
                if expected.projection_version < actual.projection_version
                else RouteReasonCodeV1.VERSION_CONFLICT
            )
        if expected.fencing_token != actual.fencing_token:
            return RouteReasonCodeV1.STALE_FENCING
        return None

    @staticmethod
    def _successor_error(candidate: RouteVersionV1, predecessor: RouteVersionV1) -> RouteReasonCodeV1 | None:
        if candidate.route_epoch <= predecessor.route_epoch:
            return RouteReasonCodeV1.STALE_ROUTE_EPOCH
        if candidate.topology_epoch < predecessor.topology_epoch:
            return RouteReasonCodeV1.STALE_TOPOLOGY_EPOCH
        if candidate.key_epoch < predecessor.key_epoch:
            return RouteReasonCodeV1.STALE_KEY_EPOCH
        if candidate.projection_version <= predecessor.projection_version:
            return RouteReasonCodeV1.STALE_PROJECTION_VERSION
        if candidate.fencing_token == predecessor.fencing_token:
            return RouteReasonCodeV1.STALE_FENCING
        return None

    def _current_version(self, key: RouteKeyV1) -> RouteVersionV1 | None:
        projection = self._routes.get(key)
        return projection.version if projection is not None else self._tombstones.get(key)

    def _now_ms(self) -> int:
        value = self._clock.now_ms()
        if type(value) is not int or value <= 0:
            raise ValueError("route_clock_invalid")
        return value

    @staticmethod
    def _result(operation, operation_id, key, outcome, reason, version, now_ms):
        return RouteMutationResultV1(
            operation=operation,
            operation_id=operation_id,
            key=key,
            outcome=outcome,
            reason_code=reason,
            observed_version=version,
            occurred_at_ms=now_ms,
            retryable=reason
            in {
                RouteReasonCodeV1.TIMEOUT,
                RouteReasonCodeV1.RUNTIME_UNAVAILABLE,
                RouteReasonCodeV1.COMMAND_REORDERED,
                RouteReasonCodeV1.PARTIAL_APPLY_ROLLED_BACK,
                RouteReasonCodeV1.VERSION_CONFLICT,
            },
        )

    def _ack(self, operation, operation_id, key, version, now_ms):
        return self._result(
            operation,
            operation_id,
            key,
            RouteOutcomeV1.ACKNOWLEDGED,
            RouteReasonCodeV1.ACKNOWLEDGED,
            version,
            now_ms,
        )

    def _rejection(self, operation, operation_id, key, reason, now_ms):
        return self._result(
            operation,
            operation_id,
            key,
            RouteOutcomeV1.REJECTED,
            reason,
            self._current_version(key),
            now_ms,
        )

    @staticmethod
    def _unknown_observation(key, reason, now_ms):
        return RouteObservationResultV1(
            key=key,
            presence=RoutePresenceV1.UNKNOWN,
            reason_code=reason,
            projection=None,
            tombstone_version=None,
            observed_at_ms=now_ms,
            retryable=True,
        )


__all__ = [
    "DeterministicSfuBroadcastRouteMockAdapter",
    "RouteClockV1",
    "RouteFaultPlanV1",
    "RouteFaultV1",
    "ScriptedRouteFaultPlanV1",
]
