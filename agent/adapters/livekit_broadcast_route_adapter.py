"""Typed Hub route-port adapter for the public LiveKit control API.

The adapter deliberately separates Hub authorization, endpoint identity,
operation persistence, and vendor transport.  A successful RoomService call is
recorded as accepted-but-unverified and never promoted to an Ananta route ACK.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Protocol

from agent.adapters.livekit_control_api_client import (
    LiveKitControlApiClient,
    LiveKitControlApiError,
    LiveKitControlResult,
)
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
    RuntimeControlModeV1,
    UpdateRouteCommandV1,
)


class LiveKitRouteAdapterClock(Protocol):
    def now_ms(self) -> int: ...


class LiveKitControlEndpointIdentityPort(Protocol):
    """Verifies the configured TLS/API endpoint identity out of band."""

    def verify(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class LiveKitRouteAuthorization:
    """Hub-verifier result bound to the exact canonical command."""

    authorized: bool
    command_digest: str
    signed_projection: RouteProjectionV1 | None
    hub_signature_verified: bool
    domain_binding_verified: bool
    current_version: RouteVersionV1 | None = None
    tombstone_version: RouteVersionV1 | None = None
    reason_code: RouteReasonCodeV1 = RouteReasonCodeV1.AUTHORIZATION_FAILED


class LiveKitRouteCommandAuthorizationPort(Protocol):
    def authorize(
        self,
        *,
        operation: RouteOperationV1,
        command: ApplyRouteCommandV1 | UpdateRouteCommandV1 | RevokeRouteCommandV1,
        command_digest: str,
    ) -> LiveKitRouteAuthorization: ...


class LiveKitRouteObservationAuthorizationPort(Protocol):
    """Read authorization is independent from command-envelope verification."""

    def authorize_observation(self, query: ObserveRouteQueryV1) -> bool: ...


@dataclass(frozen=True, slots=True)
class LiveKitRouteOperationRecord:
    """Persistent idempotency record with all security-relevant bindings."""

    operation_id: str
    idempotency_key: str
    operation: RouteOperationV1
    key: RouteKeyV1
    command_digest: str
    intent_digest: str
    audience_digest: str
    projection_version: int
    route_epoch: int
    topology_epoch: int
    key_epoch: int
    fencing_token: str
    issued_at_ms: int
    expires_at_ms: int


class LiveKitOperationReservationStatus(str, Enum):
    CREATED = "created"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class LiveKitOperationReservation:
    status: LiveKitOperationReservationStatus
    existing: LiveKitRouteOperationRecord | None = None
    previous_result: RouteMutationResultV1 | None = None


class LiveKitRouteOperationLedgerPort(Protocol):
    """Atomic, durable operation ledger owned by the Hub."""

    def reserve(
        self, record: LiveKitRouteOperationRecord
    ) -> LiveKitOperationReservation: ...

    def complete(
        self,
        record: LiveKitRouteOperationRecord,
        result: RouteMutationResultV1,
    ) -> None: ...


class LiveKitBroadcastRouteAdapter:
    """Adapts the public LiveKit API to the four segregated route ports."""

    def __init__(
        self,
        *,
        client: LiveKitControlApiClient,
        command_authorization: LiveKitRouteCommandAuthorizationPort,
        observation_authorization: LiveKitRouteObservationAuthorizationPort,
        endpoint_identity: LiveKitControlEndpointIdentityPort,
        operation_ledger: LiveKitRouteOperationLedgerPort,
        clock: LiveKitRouteAdapterClock,
    ) -> None:
        self._client = client
        self._command_authorization = command_authorization
        self._observation_authorization = observation_authorization
        self._endpoint_identity = endpoint_identity
        self._operation_ledger = operation_ledger
        self._clock = clock

    def capabilities(self) -> dict[str, object]:
        client = dict(self._client.capabilities())
        observation_supported = client.get("route_observe") != "unsupported"
        return {
            "schema": "ananta.livekit-broadcast-route-adapter-capabilities.v1",
            "runtime_control_mode": RuntimeControlModeV1.LIVEKIT_CONTROL_API.value,
            "supported": observation_supported,
            "command_supported": True,
            "authoritative_observation_supported": observation_supported,
            "reason_code": (
                "route_adapter_supported"
                if observation_supported
                else RouteReasonCodeV1.OBSERVATION_UNSUPPORTED.value
            ),
            "client": client,
        }

    def apply(self, command: ApplyRouteCommandV1) -> RouteMutationResultV1:
        return self._mutate(
            operation=RouteOperationV1.APPLY,
            command=command,
            key=command.desired.key,
            projection=command.desired,
            invoke=lambda: self._client.apply(command),
        )

    def update(self, command: UpdateRouteCommandV1) -> RouteMutationResultV1:
        return self._mutate(
            operation=RouteOperationV1.UPDATE,
            command=command,
            key=command.desired.key,
            projection=command.desired,
            invoke=lambda: self._client.update(command),
        )

    def revoke(self, command: RevokeRouteCommandV1) -> RouteMutationResultV1:
        return self._mutate(
            operation=RouteOperationV1.REVOKE,
            command=command,
            key=command.key,
            projection=None,
            invoke=lambda: self._client.revoke(command),
        )

    def observe(self, query: ObserveRouteQueryV1) -> RouteObservationResultV1:
        now_ms = self._now_ms()
        try:
            endpoint_verified = self._endpoint_identity.verify()
            authorized = self._observation_authorization.authorize_observation(query)
        except Exception:
            return self._unknown_observation(
                query.key, RouteReasonCodeV1.RUNTIME_UNAVAILABLE, now_ms, True
            )
        if not endpoint_verified:
            return self._unknown_observation(
                query.key, RouteReasonCodeV1.ENDPOINT_IDENTITY_INVALID, now_ms, False
            )
        if not authorized:
            return self._unknown_observation(
                query.key, RouteReasonCodeV1.AUTHORIZATION_FAILED, now_ms, False
            )
        try:
            observation = self._client.observe(query)
        except LiveKitControlApiError as exc:
            return self._unknown_observation(
                query.key,
                RouteReasonCodeV1.RUNTIME_UNAVAILABLE,
                now_ms,
                exc.retryable,
            )
        if not observation.authoritative:
            return self._unknown_observation(
                query.key, RouteReasonCodeV1.OBSERVATION_UNSUPPORTED, now_ms, False
            )
        # The pinned public client currently never reaches this branch.  It is
        # intentionally fail-closed until a future client supplies an exact,
        # authoritative RouteProjectionV1 rather than participant presence.
        return self._unknown_observation(
            query.key, RouteReasonCodeV1.OBSERVATION_UNSUPPORTED, now_ms, False
        )

    def _mutate(
        self,
        *,
        operation: RouteOperationV1,
        command: ApplyRouteCommandV1 | UpdateRouteCommandV1 | RevokeRouteCommandV1,
        key: RouteKeyV1,
        projection: RouteProjectionV1 | None,
        invoke,
    ) -> RouteMutationResultV1:
        now_ms = self._now_ms()
        digest = _canonical_digest(command)
        authorization, rejection = self._authorize(
            operation, command, key, projection, digest, now_ms
        )
        if rejection is not None:
            return rejection
        assert authorization is not None
        assert authorization.signed_projection is not None
        record = self._operation_record(
            operation, command, authorization.signed_projection, digest
        )
        try:
            reservation = self._operation_ledger.reserve(record)
        except Exception:
            return self._result(
                operation,
                command.operation_id,
                key,
                RouteOutcomeV1.UNKNOWN,
                RouteReasonCodeV1.RUNTIME_UNAVAILABLE,
                now_ms,
                retryable=True,
            )
        if reservation.status is LiveKitOperationReservationStatus.CONFLICT:
            return self._result(
                operation,
                command.operation_id,
                key,
                RouteOutcomeV1.REJECTED,
                RouteReasonCodeV1.COMMAND_ID_CONFLICT,
                now_ms,
            )
        if reservation.status is LiveKitOperationReservationStatus.DUPLICATE:
            if reservation.existing != record:
                return self._result(
                    operation,
                    command.operation_id,
                    key,
                    RouteOutcomeV1.REJECTED,
                    RouteReasonCodeV1.COMMAND_ID_CONFLICT,
                    now_ms,
                )
            previous = reservation.previous_result
            return self._result(
                operation,
                command.operation_id,
                key,
                previous.outcome if previous is not None else RouteOutcomeV1.UNKNOWN,
                RouteReasonCodeV1.DUPLICATE_IDEMPOTENT,
                now_ms,
                observed_version=(
                    previous.observed_version if previous is not None else None
                ),
                retryable=(previous.retryable if previous is not None else True),
            )
        try:
            control_result = invoke()
        except LiveKitControlApiError as exc:
            result = self._result(
                operation,
                command.operation_id,
                key,
                RouteOutcomeV1.REJECTED,
                self._map_error_reason(exc.reason_code),
                now_ms,
                retryable=exc.retryable,
            )
        else:
            result = self._control_result(
                operation, command.operation_id, key, control_result, now_ms
            )
        try:
            self._operation_ledger.complete(record, result)
        except Exception:
            return self._result(
                operation,
                command.operation_id,
                key,
                RouteOutcomeV1.UNKNOWN,
                RouteReasonCodeV1.RUNTIME_UNAVAILABLE,
                now_ms,
                retryable=True,
            )
        return result

    def _authorize(
        self,
        operation: RouteOperationV1,
        command: ApplyRouteCommandV1 | UpdateRouteCommandV1 | RevokeRouteCommandV1,
        key: RouteKeyV1,
        projection: RouteProjectionV1 | None,
        digest: str,
        now_ms: int,
    ) -> tuple[LiveKitRouteAuthorization | None, RouteMutationResultV1 | None]:
        try:
            endpoint_verified = self._endpoint_identity.verify()
            decision = self._command_authorization.authorize(
                operation=operation, command=command, command_digest=digest
            )
        except Exception:
            return None, self._result(
                operation,
                command.operation_id,
                key,
                RouteOutcomeV1.UNKNOWN,
                RouteReasonCodeV1.RUNTIME_UNAVAILABLE,
                now_ms,
                retryable=True,
            )
        reason: RouteReasonCodeV1 | None = None
        if not endpoint_verified:
            reason = RouteReasonCodeV1.ENDPOINT_IDENTITY_INVALID
        elif not (
            decision.authorized
            and decision.hub_signature_verified
            and decision.domain_binding_verified
            and decision.command_digest == digest
            and decision.signed_projection is not None
            and decision.signed_projection.key == key
        ):
            reason = decision.reason_code
        elif projection is not None and decision.signed_projection != projection:
            reason = RouteReasonCodeV1.AUTHORIZATION_FAILED
        elif operation is RouteOperationV1.REVOKE and (
            decision.signed_projection.version != command.expected_version
        ):
            reason = RouteReasonCodeV1.AUTHORIZATION_FAILED
        elif operation in (RouteOperationV1.APPLY, RouteOperationV1.UPDATE) and (
            decision.signed_projection.runtime_control_mode
            is not RuntimeControlModeV1.LIVEKIT_CONTROL_API
        ):
            reason = RouteReasonCodeV1.AUTHORIZATION_FAILED
        elif operation in (RouteOperationV1.APPLY, RouteOperationV1.UPDATE) and (
            now_ms < decision.signed_projection.issued_at_ms
        ):
            reason = RouteReasonCodeV1.NOT_YET_VALID
        elif operation in (RouteOperationV1.APPLY, RouteOperationV1.UPDATE) and (
            now_ms >= decision.signed_projection.expires_at_ms
        ):
            reason = RouteReasonCodeV1.EXPIRED
        elif operation is RouteOperationV1.APPLY:
            if decision.current_version is not None:
                reason = RouteReasonCodeV1.ALREADY_EXISTS
            elif decision.tombstone_version is not None:
                reason = _successor_error(
                    decision.signed_projection.version, decision.tombstone_version
                )
        else:
            expected = command.expected_version
            reason = _expected_version_error(expected, decision.current_version)
        if reason is None:
            return decision, None
        return None, self._result(
            operation,
            command.operation_id,
            key,
            RouteOutcomeV1.REJECTED,
            reason,
            now_ms,
            observed_version=decision.current_version,
        )

    @staticmethod
    def _operation_record(
        operation: RouteOperationV1,
        command: ApplyRouteCommandV1 | UpdateRouteCommandV1 | RevokeRouteCommandV1,
        projection: RouteProjectionV1,
        command_digest: str,
    ) -> LiveKitRouteOperationRecord:
        version = (
            command.revoke_version
            if isinstance(command, RevokeRouteCommandV1)
            else projection.version
        )
        return LiveKitRouteOperationRecord(
            operation_id=command.operation_id,
            idempotency_key=command.operation_id,
            operation=operation,
            key=projection.key,
            command_digest=command_digest,
            intent_digest=projection.intent_digest,
            audience_digest=projection.audience_digest,
            projection_version=version.projection_version,
            route_epoch=version.route_epoch,
            topology_epoch=version.topology_epoch,
            key_epoch=version.key_epoch,
            fencing_token=version.fencing_token,
            issued_at_ms=projection.issued_at_ms,
            expires_at_ms=projection.expires_at_ms,
        )

    def _control_result(
        self,
        operation: RouteOperationV1,
        operation_id: str,
        key: RouteKeyV1,
        control: LiveKitControlResult,
        now_ms: int,
    ) -> RouteMutationResultV1:
        if control.accepted_by_api:
            return self._result(
                operation,
                operation_id,
                key,
                RouteOutcomeV1.UNKNOWN,
                RouteReasonCodeV1.CONTROL_API_ACCEPTED_UNVERIFIED,
                now_ms,
                retryable=True,
            )
        return self._result(
            operation,
            operation_id,
            key,
            RouteOutcomeV1.REJECTED,
            self._map_error_reason(control.reason_code),
            now_ms,
            retryable=control.retryable,
        )

    @staticmethod
    def _map_error_reason(reason_code: str) -> RouteReasonCodeV1:
        if "expired" in reason_code:
            return RouteReasonCodeV1.EXPIRED
        if "not_yet_valid" in reason_code:
            return RouteReasonCodeV1.NOT_YET_VALID
        if "partial" in reason_code:
            return RouteReasonCodeV1.PARTIAL_APPLY_ROLLED_BACK
        return RouteReasonCodeV1.RUNTIME_UNAVAILABLE

    def _now_ms(self) -> int:
        value = self._clock.now_ms()
        if type(value) is not int or value <= 0:
            raise ValueError("livekit_route_adapter_clock_invalid")
        return value

    @staticmethod
    def _result(
        operation: RouteOperationV1,
        operation_id: str,
        key: RouteKeyV1,
        outcome: RouteOutcomeV1,
        reason: RouteReasonCodeV1,
        now_ms: int,
        *,
        observed_version: RouteVersionV1 | None = None,
        retryable: bool = False,
    ) -> RouteMutationResultV1:
        return RouteMutationResultV1(
            operation=operation,
            operation_id=operation_id,
            key=key,
            outcome=outcome,
            reason_code=reason,
            observed_version=observed_version,
            occurred_at_ms=now_ms,
            retryable=retryable,
        )

    @staticmethod
    def _unknown_observation(
        key: RouteKeyV1,
        reason: RouteReasonCodeV1,
        now_ms: int,
        retryable: bool,
    ) -> RouteObservationResultV1:
        return RouteObservationResultV1(
            key=key,
            presence=RoutePresenceV1.UNKNOWN,
            reason_code=reason,
            projection=None,
            tombstone_version=None,
            observed_at_ms=now_ms,
            retryable=retryable,
        )


def _canonical_digest(value: object) -> str:
    payload = asdict(value) if hasattr(value, "__dataclass_fields__") else value
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _expected_version_error(
    expected: RouteVersionV1, actual: RouteVersionV1 | None
) -> RouteReasonCodeV1 | None:
    if actual is None:
        return RouteReasonCodeV1.NOT_FOUND
    comparisons = (
        (expected.route_epoch, actual.route_epoch, RouteReasonCodeV1.STALE_ROUTE_EPOCH),
        (
            expected.topology_epoch,
            actual.topology_epoch,
            RouteReasonCodeV1.STALE_TOPOLOGY_EPOCH,
        ),
        (expected.key_epoch, actual.key_epoch, RouteReasonCodeV1.STALE_KEY_EPOCH),
        (
            expected.projection_version,
            actual.projection_version,
            RouteReasonCodeV1.STALE_PROJECTION_VERSION,
        ),
    )
    for candidate, current, stale_reason in comparisons:
        if candidate != current:
            return stale_reason if candidate < current else RouteReasonCodeV1.VERSION_CONFLICT
    if expected.fencing_token != actual.fencing_token:
        return RouteReasonCodeV1.STALE_FENCING
    return None


def _successor_error(
    candidate: RouteVersionV1, predecessor: RouteVersionV1
) -> RouteReasonCodeV1 | None:
    if candidate.route_epoch <= predecessor.route_epoch:
        return RouteReasonCodeV1.STALE_ROUTE_EPOCH
    if candidate.projection_version <= predecessor.projection_version:
        return RouteReasonCodeV1.STALE_PROJECTION_VERSION
    if candidate.topology_epoch < predecessor.topology_epoch:
        return RouteReasonCodeV1.STALE_TOPOLOGY_EPOCH
    if candidate.key_epoch < predecessor.key_epoch:
        return RouteReasonCodeV1.STALE_KEY_EPOCH
    if candidate.fencing_token == predecessor.fencing_token:
        return RouteReasonCodeV1.STALE_FENCING
    return None


__all__ = [
    "LiveKitBroadcastRouteAdapter",
    "LiveKitControlEndpointIdentityPort",
    "LiveKitOperationReservation",
    "LiveKitOperationReservationStatus",
    "LiveKitRouteAdapterClock",
    "LiveKitRouteAuthorization",
    "LiveKitRouteCommandAuthorizationPort",
    "LiveKitRouteObservationAuthorizationPort",
    "LiveKitRouteOperationLedgerPort",
    "LiveKitRouteOperationRecord",
]
