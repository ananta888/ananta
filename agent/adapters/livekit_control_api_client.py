"""Pinned, least-privilege LiveKit RoomService control boundary.

This adapter executes only Hub-authorized subscription projections. It neither
stores Ananta policy nor claims that a successful Twirp call is a runtime ACK.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol
from urllib.parse import urlsplit

import jwt
import requests

from agent.services.sfu_broadcast_route_port import (
    ApplyRouteCommandV1,
    ObserveRouteQueryV1,
    RevokeRouteCommandV1,
    RouteKeyV1,
    RouteProjectionV1,
    RouteVersionV1,
    RuntimeControlModeV1,
    UpdateRouteCommandV1,
)

PINNED_LIVEKIT_SERVER_VERSION = "1.13.1"
PINNED_LIVEKIT_IMAGE = (
    "livekit/livekit-server@sha256:"
    "2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3"
)
ROOM_SERVICE_PREFIX = "/twirp/livekit.RoomService"
MAX_REQUEST_BYTES = 65_536
MAX_RESPONSE_BYTES = 1_048_576
MAX_TRACKS_PER_ROUTE = 64
MAX_RECEIVERS_PER_ROUTE = 7
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,255}$")
_TRACK_SID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class LiveKitControlApiError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        self.reason_code = reason_code
        self.retryable = retryable
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class LiveKitControlApiConfig:
    endpoint: str
    api_key: str
    api_secret: str = field(repr=False)
    server_version: str = PINNED_LIVEKIT_SERVER_VERSION
    verify_tls: bool | str | Path = True
    timeout_seconds: float = 5.0
    token_ttl_seconds: int = 20

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise LiveKitControlApiError("livekit_https_endpoint_required")
        if self.server_version != PINNED_LIVEKIT_SERVER_VERSION:
            raise LiveKitControlApiError("livekit_server_version_skew")
        if not _IDENTIFIER.fullmatch(self.api_key):
            raise LiveKitControlApiError("livekit_api_key_invalid")
        if len(self.api_secret) < 32:
            raise LiveKitControlApiError("livekit_api_secret_invalid")
        if self.verify_tls is False:
            raise LiveKitControlApiError("livekit_tls_verification_required")
        if not 0.2 <= float(self.timeout_seconds) <= 30.0:
            raise LiveKitControlApiError("livekit_timeout_invalid")
        if not 5 <= self.token_ttl_seconds <= 30:
            raise LiveKitControlApiError("livekit_token_ttl_invalid")


@dataclass(frozen=True, slots=True)
class LiveKitRouteBinding:
    """Exact vendor identifiers resolved outside the adapter by the Hub."""

    room_name: str
    receiver_identities: tuple[str, ...]
    track_sids: tuple[str, ...]
    subscription_only_safe: bool

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.room_name):
            raise LiveKitControlApiError("livekit_room_name_invalid")
        if (
            not 1 <= len(self.receiver_identities) <= MAX_RECEIVERS_PER_ROUTE
            or len(set(self.receiver_identities)) != len(self.receiver_identities)
            or any(not _IDENTIFIER.fullmatch(item) for item in self.receiver_identities)
        ):
            raise LiveKitControlApiError("livekit_receiver_binding_invalid")
        if (
            not 1 <= len(self.track_sids) <= MAX_TRACKS_PER_ROUTE
            or len(set(self.track_sids)) != len(self.track_sids)
            or any(not _TRACK_SID.fullmatch(item) for item in self.track_sids)
        ):
            raise LiveKitControlApiError("livekit_track_binding_invalid")
        if not self.subscription_only_safe:
            raise LiveKitControlApiError("livekit_subscription_projection_would_widen_rights")


class LiveKitRouteBindingResolver(Protocol):
    def desired(self, projection: RouteProjectionV1) -> LiveKitRouteBinding: ...

    def existing(self, key: RouteKeyV1, version: RouteVersionV1) -> LiveKitRouteBinding: ...


@dataclass(frozen=True, slots=True)
class TwirpResponse:
    status_code: int
    payload: Mapping[str, object]
    content_length: int


class TwirpTransport(Protocol):
    def post(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
        verify_tls: bool | str | Path,
    ) -> TwirpResponse: ...


class RequestsTwirpTransport:
    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    def post(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
        verify_tls: bool | str | Path,
    ) -> TwirpResponse:
        try:
            response = self._session.post(
                url,
                headers=dict(headers),
                json=dict(payload),
                timeout=timeout_seconds,
                verify=str(verify_tls) if isinstance(verify_tls, Path) else verify_tls,
                allow_redirects=False,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise LiveKitControlApiError("livekit_control_unavailable", retryable=True) from exc
        if 300 <= response.status_code < 400:
            raise LiveKitControlApiError("livekit_redirect_forbidden")
        raw = response.content
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LiveKitControlApiError("livekit_response_oversize")
        try:
            decoded = response.json() if raw else {}
        except ValueError as exc:
            raise LiveKitControlApiError("livekit_response_json_invalid") from exc
        if not isinstance(decoded, Mapping):
            raise LiveKitControlApiError("livekit_response_json_invalid")
        return TwirpResponse(response.status_code, decoded, len(raw))


@dataclass(frozen=True, slots=True)
class LiveKitControlResult:
    operation: str
    accepted_by_api: bool
    authoritative_runtime_ack: bool
    reason_code: str
    calls_completed: int
    rollback_completed: bool = False
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class LiveKitRouteObservation:
    presence: str
    reason_code: str
    participant_identities: tuple[str, ...] = ()
    authoritative: bool = False


class LiveKitControlApiClient:
    """Fixed RoomService surface; there is no arbitrary endpoint escape hatch."""

    def __init__(
        self,
        config: LiveKitControlApiConfig,
        bindings: LiveKitRouteBindingResolver,
        *,
        transport: TwirpTransport | None = None,
        clock=time.time,
    ) -> None:
        self._config = config
        self._bindings = bindings
        self._transport = transport or RequestsTwirpTransport()
        self._clock = clock

    def capabilities(self) -> Mapping[str, object]:
        return {
            "schema": "ananta.livekit-control-api-capabilities.v1",
            "runtime_control_mode": RuntimeControlModeV1.LIVEKIT_CONTROL_API.value,
            "server_version": PINNED_LIVEKIT_SERVER_VERSION,
            "server_image": PINNED_LIVEKIT_IMAGE,
            "api": "livekit.RoomService/Twirp JSON",
            "route_apply": "accepted_unverified",
            "route_update": "accepted_unverified",
            "route_revoke": "accepted_unverified",
            "route_observe": "unsupported",
            "drain": "unsupported_native_signal_only",
            "hub_fencing": "unsupported",
        }

    def apply(self, command: ApplyRouteCommandV1) -> LiveKitControlResult:
        self._validate_projection(command.desired)
        binding = self._bindings.desired(command.desired)
        return self._set_subscriptions("apply", binding, subscribe=True)

    def update(self, command: UpdateRouteCommandV1) -> LiveKitControlResult:
        self._validate_projection(command.desired)
        previous = self._bindings.existing(command.desired.key, command.expected_version)
        desired = self._bindings.desired(command.desired)
        revoked = self._set_subscriptions("update_revoke_previous", previous, subscribe=False)
        if not revoked.accepted_by_api:
            return LiveKitControlResult(
                "update",
                False,
                False,
                revoked.reason_code,
                revoked.calls_completed,
                retryable=revoked.retryable,
            )
        applied = self._set_subscriptions("update_apply_desired", desired, subscribe=True)
        if applied.accepted_by_api:
            return LiveKitControlResult("update", True, False, "livekit_command_accepted_unverified", applied.calls_completed + revoked.calls_completed)
        rollback = self._set_subscriptions("update_rollback", previous, subscribe=True)
        return LiveKitControlResult(
            "update",
            False,
            False,
            "livekit_partial_update_rolled_back" if rollback.accepted_by_api else "livekit_partial_update_rollback_failed",
            revoked.calls_completed + applied.calls_completed + rollback.calls_completed,
            rollback_completed=rollback.accepted_by_api,
            retryable=applied.retryable,
        )

    def revoke(self, command: RevokeRouteCommandV1) -> LiveKitControlResult:
        binding = self._bindings.existing(command.key, command.expected_version)
        return self._set_subscriptions("revoke", binding, subscribe=False)

    def observe(self, query: ObserveRouteQueryV1) -> LiveKitRouteObservation:
        binding = self._bindings.existing(query.key, _unknown_version_for_resolver())
        response = self._call("ListParticipants", {"room": binding.room_name}, room=binding.room_name)
        participants = response.get("participants")
        identities = tuple(
            sorted(
                str(item.get("identity"))
                for item in participants or ()
                if isinstance(item, Mapping) and isinstance(item.get("identity"), str)
            )
        )
        return LiveKitRouteObservation(
            "unknown",
            "livekit_route_observation_unsupported",
            identities,
            authoritative=False,
        )

    def health(self) -> Mapping[str, object]:
        self._call("ListRooms", {}, room=None)
        return {
            "ready": True,
            "runtime_control_mode": RuntimeControlModeV1.LIVEKIT_CONTROL_API.value,
            "server_version_binding": PINNED_LIVEKIT_SERVER_VERSION,
            "authoritative_route_observation": False,
        }

    def drain(self) -> LiveKitControlResult:
        return LiveKitControlResult(
            "drain",
            False,
            False,
            "livekit_drain_control_api_unsupported",
            0,
        )

    def _validate_projection(self, projection: RouteProjectionV1) -> None:
        if projection.runtime_control_mode is not RuntimeControlModeV1.LIVEKIT_CONTROL_API:
            raise LiveKitControlApiError("livekit_runtime_mode_mismatch")
        now_ms = int(self._clock() * 1000)
        if now_ms < projection.issued_at_ms:
            raise LiveKitControlApiError("livekit_route_not_yet_valid")
        if now_ms >= projection.expires_at_ms:
            raise LiveKitControlApiError("livekit_route_expired")

    def _set_subscriptions(
        self, operation: str, binding: LiveKitRouteBinding, *, subscribe: bool
    ) -> LiveKitControlResult:
        completed: list[str] = []
        try:
            for receiver in binding.receiver_identities:
                self._call(
                    "UpdateSubscriptions",
                    {
                        "room": binding.room_name,
                        "identity": receiver,
                        "track_sids": list(binding.track_sids),
                        "subscribe": subscribe,
                    },
                    room=binding.room_name,
                )
                completed.append(receiver)
        except LiveKitControlApiError as exc:
            rollback_ok = True
            if subscribe:
                for receiver in reversed(completed):
                    try:
                        self._call(
                            "UpdateSubscriptions",
                            {
                                "room": binding.room_name,
                                "identity": receiver,
                                "track_sids": list(binding.track_sids),
                                "subscribe": False,
                            },
                            room=binding.room_name,
                        )
                    except LiveKitControlApiError:
                        rollback_ok = False
            return LiveKitControlResult(
                operation,
                False,
                False,
                "livekit_partial_apply_rolled_back" if completed and rollback_ok else exc.reason_code,
                len(completed),
                rollback_completed=bool(completed) and rollback_ok,
                retryable=exc.retryable,
            )
        return LiveKitControlResult(
            operation,
            True,
            False,
            "livekit_command_accepted_unverified",
            len(completed),
        )

    def _call(
        self, method: str, payload: Mapping[str, object], *, room: str | None
    ) -> Mapping[str, object]:
        if method not in {"UpdateSubscriptions", "ListParticipants", "ListRooms"}:
            raise LiveKitControlApiError("livekit_control_operation_unsupported")
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise LiveKitControlApiError("livekit_request_oversize")
        token = self._room_admin_token(room)
        response = self._transport.post(
            url=f"{self._config.endpoint.rstrip('/')}{ROOM_SERVICE_PREFIX}/{method}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"ananta-livekit-control/{PINNED_LIVEKIT_SERVER_VERSION}",
            },
            payload=payload,
            timeout_seconds=self._config.timeout_seconds,
            verify_tls=self._config.verify_tls,
        )
        if response.content_length > MAX_RESPONSE_BYTES:
            raise LiveKitControlApiError("livekit_response_oversize")
        if response.status_code == 429:
            raise LiveKitControlApiError("livekit_control_rate_limited", retryable=True)
        if response.status_code in {401, 403}:
            raise LiveKitControlApiError("livekit_control_credential_rejected")
        if response.status_code == 404:
            raise LiveKitControlApiError("livekit_control_capability_unsupported")
        if not 200 <= response.status_code < 300:
            raise LiveKitControlApiError(
                "livekit_control_unavailable",
                retryable=response.status_code >= 500,
            )
        return response.payload

    def _room_admin_token(self, room: str | None) -> str:
        now = int(self._clock())
        grant: dict[str, object] = {"roomAdmin": True}
        if room is not None:
            grant["room"] = room
        return jwt.encode(
            {
                "iss": self._config.api_key,
                "nbf": now - 1,
                "exp": now + self._config.token_ttl_seconds,
                "video": grant,
            },
            self._config.api_secret,
            algorithm="HS256",
        )


class UnsupportedSfuRuntimeControlBoundary:
    def __init__(self, reason_code: str = "sfu_runtime_control_unsupported") -> None:
        self.reason_code = reason_code

    def capabilities(self) -> Mapping[str, object]:
        return {
            "runtime_control_mode": "unsupported",
            "available": False,
            "reason_code": self.reason_code,
        }

    def _raise(self):
        raise LiveKitControlApiError(self.reason_code)

    apply = update = revoke = observe = health = drain = _raise


def build_sfu_runtime_control_boundary(
    mode: str,
    *,
    config: LiveKitControlApiConfig | None = None,
    bindings: LiveKitRouteBindingResolver | None = None,
    transport: TwirpTransport | None = None,
):
    if mode == RuntimeControlModeV1.LIVEKIT_CONTROL_API.value:
        if config is None or bindings is None:
            return UnsupportedSfuRuntimeControlBoundary("livekit_control_configuration_missing")
        return LiveKitControlApiClient(config, bindings, transport=transport)
    return UnsupportedSfuRuntimeControlBoundary("sfu_runtime_mode_not_selected")


def _unknown_version_for_resolver() -> RouteVersionV1:
    """Observation has no version; resolvers must treat this sentinel as lookup-only."""

    return RouteVersionV1(1, 1, 1, 1, "observe-only")


__all__ = [
    "LiveKitControlApiClient",
    "LiveKitControlApiConfig",
    "LiveKitControlApiError",
    "LiveKitControlResult",
    "LiveKitRouteBinding",
    "LiveKitRouteBindingResolver",
    "LiveKitRouteObservation",
    "PINNED_LIVEKIT_IMAGE",
    "PINNED_LIVEKIT_SERVER_VERSION",
    "RequestsTwirpTransport",
    "TwirpResponse",
    "TwirpTransport",
    "UnsupportedSfuRuntimeControlBoundary",
    "build_sfu_runtime_control_boundary",
]
