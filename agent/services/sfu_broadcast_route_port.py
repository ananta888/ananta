"""Vendor-neutral runtime ports for Hub-authorized SFU broadcast routes.

The types in this module are the narrow boundary after the Hub has validated a
signed ``fanout_route_intent.v1``.  Adapters execute an absolute projection;
they do not resolve policy, infer recipients, widen layers, extend expiry, or
mint epochs.  The four protocols deliberately remain separate so consumers
only depend on the operation they need.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Protocol, runtime_checkable


ROUTE_PORT_CONTRACT_V1 = "ananta.sfu-broadcast-route-port.v1"
MAX_ROUTE_RECEIVERS_V1 = 7
MAX_ROUTE_LAYERS_V1 = 3
MAX_ROUTE_TRAFFIC_CLASSES_V1 = 4
MAX_ROUTE_TTL_MS_V1 = 10_000
MAX_ROUTE_BITRATE_BPS_V1 = 100_000_000
MAX_ROUTE_PACKETS_PER_SECOND_V1 = 100_000
MAX_ROUTE_BURST_BYTES_V1 = 1_048_576

_DIGEST_HEX = re.compile(r"^[a-f0-9]{64}$")
_DIGEST_B64URL = re.compile(r"^[A-Za-z0-9_-]{43}$")
_RECEIVER_REF = re.compile(r"^[A-Za-z0-9_-]{22}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class RouteContractViolationV1(ValueError):
    """Raised when an in-process caller violates the v1 port contract."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _require(condition: bool, reason_code: str) -> None:
    if not condition:
        raise RouteContractViolationV1(reason_code)


def _valid_identifier(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


class RuntimeControlModeV1(str, Enum):
    LIVEKIT_CONTROL_API = "livekit_control_api"
    AUTHENTICATED_RUNTIME_EXTENSION = "authenticated_runtime_extension"


class MediaKindV1(str, Enum):
    AUDIO = "audio"
    VIDEO = "video"


class RouteOperationV1(str, Enum):
    APPLY = "apply"
    UPDATE = "update"
    REVOKE = "revoke"
    OBSERVE = "observe"


class RouteOutcomeV1(str, Enum):
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class RoutePresenceV1(str, Enum):
    ACTIVE = "active"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class RouteReasonCodeV1(str, Enum):
    """Stable, transport-independent result codes exposed by every adapter."""

    ACKNOWLEDGED = "route_acknowledged"
    ACTIVE = "route_active"
    ABSENT = "route_absent"
    DUPLICATE_IDEMPOTENT = "route_duplicate_idempotent"
    COMMAND_ID_CONFLICT = "route_command_id_conflict"
    ALREADY_EXISTS = "route_already_exists"
    NOT_FOUND = "route_not_found"
    NOT_YET_VALID = "route_not_yet_valid"
    EXPIRED = "route_expired"
    STALE_ROUTE_EPOCH = "route_stale_route_epoch"
    STALE_TOPOLOGY_EPOCH = "route_stale_topology_epoch"
    STALE_KEY_EPOCH = "route_stale_key_epoch"
    STALE_PROJECTION_VERSION = "route_stale_projection_version"
    STALE_FENCING = "route_stale_fencing"
    VERSION_CONFLICT = "route_version_conflict"
    COMMAND_REORDERED = "route_command_reordered"
    TIMEOUT = "route_timeout"
    PARTIAL_APPLY_ROLLED_BACK = "route_partial_apply_rolled_back"
    RUNTIME_UNAVAILABLE = "route_runtime_unavailable"
    RUNTIME_RECOVERED = "route_runtime_recovered"


@dataclass(frozen=True, slots=True)
class RouteKeyV1:
    tenant_ref: str
    room_ref: str
    route_id: str

    contract_version: ClassVar[str] = ROUTE_PORT_CONTRACT_V1

    def __post_init__(self) -> None:
        _require(_valid_identifier(self.tenant_ref), "route_tenant_ref_invalid")
        _require(_valid_identifier(self.room_ref), "route_room_ref_invalid")
        _require(_valid_identifier(self.route_id), "route_id_invalid")


@dataclass(frozen=True, slots=True)
class RouteVersionV1:
    """Hub-minted version and fence, echoed exactly by the runtime adapter."""

    projection_version: int
    route_epoch: int
    topology_epoch: int
    key_epoch: int
    fencing_token: str

    contract_version: ClassVar[str] = ROUTE_PORT_CONTRACT_V1

    def __post_init__(self) -> None:
        _require(_positive_int(self.projection_version), "route_projection_version_invalid")
        _require(_positive_int(self.route_epoch), "route_epoch_invalid")
        _require(_positive_int(self.topology_epoch), "route_topology_epoch_invalid")
        _require(_positive_int(self.key_epoch), "route_key_epoch_invalid")
        _require(_valid_identifier(self.fencing_token), "route_fencing_token_invalid")


@dataclass(frozen=True, slots=True)
class RouteLayerV1:
    """One exact encoding tuple, never a range or a maximum-layer selector."""

    layer_ref: str
    spatial_id: int
    temporal_id: int
    rid: str | None = None

    contract_version: ClassVar[str] = ROUTE_PORT_CONTRACT_V1

    def __post_init__(self) -> None:
        _require(_valid_identifier(self.layer_ref), "route_layer_ref_invalid")
        _require(type(self.spatial_id) is int and 0 <= self.spatial_id <= 2, "route_spatial_id_invalid")
        _require(type(self.temporal_id) is int and 0 <= self.temporal_id <= 2, "route_temporal_id_invalid")
        if self.rid is not None:
            _require(
                isinstance(self.rid, str)
                and 1 <= len(self.rid) <= 16
                and re.fullmatch(r"[A-Za-z0-9_-]+", self.rid) is not None,
                "route_rid_invalid",
            )


@dataclass(frozen=True, slots=True)
class RouteTrafficBudgetV1:
    traffic_class: str
    max_bitrate_bps: int
    max_packets_per_second: int
    max_burst_bytes: int

    contract_version: ClassVar[str] = ROUTE_PORT_CONTRACT_V1

    def __post_init__(self) -> None:
        _require(_valid_identifier(self.traffic_class), "route_traffic_class_invalid")
        _require(
            _positive_int(self.max_bitrate_bps)
            and self.max_bitrate_bps <= MAX_ROUTE_BITRATE_BPS_V1,
            "route_traffic_bitrate_invalid",
        )
        _require(
            _positive_int(self.max_packets_per_second)
            and self.max_packets_per_second <= MAX_ROUTE_PACKETS_PER_SECOND_V1,
            "route_traffic_packet_rate_invalid",
        )
        _require(
            _positive_int(self.max_burst_bytes)
            and self.max_burst_bytes <= MAX_ROUTE_BURST_BYTES_V1,
            "route_traffic_burst_invalid",
        )


@dataclass(frozen=True, slots=True)
class RouteProjectionV1:
    """An immutable, absolute projection already authorized by the Hub."""

    key: RouteKeyV1
    group_ref: str
    group_revision: int
    group_member_digest: str
    snapshot_ref: str
    audience_projection_version: int
    audience_digest: str
    receiver_refs: tuple[str, ...]
    runtime_control_mode: RuntimeControlModeV1
    cluster_ref: str
    region_ref: str
    runtime_instance_ref: str | None
    publication_ref: str
    media_kind: MediaKindV1
    allowed_layers: tuple[RouteLayerV1, ...]
    traffic_budgets: tuple[RouteTrafficBudgetV1, ...]
    max_total_bitrate_bps: int
    issued_at_ms: int
    expires_at_ms: int
    version: RouteVersionV1
    intent_digest: str

    contract_version: ClassVar[str] = ROUTE_PORT_CONTRACT_V1

    def __post_init__(self) -> None:
        _require(_valid_identifier(self.group_ref), "route_group_ref_invalid")
        _require(_positive_int(self.group_revision), "route_group_revision_invalid")
        _require(
            isinstance(self.group_member_digest, str) and _DIGEST_HEX.fullmatch(self.group_member_digest) is not None,
            "route_group_digest_invalid",
        )
        _require(_valid_identifier(self.snapshot_ref), "route_snapshot_ref_invalid")
        _require(_positive_int(self.audience_projection_version), "route_audience_version_invalid")
        _require(
            isinstance(self.audience_digest, str) and _DIGEST_B64URL.fullmatch(self.audience_digest) is not None,
            "route_audience_digest_invalid",
        )
        _require(
            isinstance(self.receiver_refs, tuple)
            and 1 <= len(self.receiver_refs) <= MAX_ROUTE_RECEIVERS_V1
            and len(set(self.receiver_refs)) == len(self.receiver_refs)
            and all(isinstance(ref, str) and _RECEIVER_REF.fullmatch(ref) is not None for ref in self.receiver_refs),
            "route_receiver_refs_invalid",
        )
        _require(isinstance(self.runtime_control_mode, RuntimeControlModeV1), "route_runtime_mode_invalid")
        _require(_valid_identifier(self.cluster_ref), "route_cluster_ref_invalid")
        _require(_valid_identifier(self.region_ref), "route_region_ref_invalid")
        if self.runtime_control_mode is RuntimeControlModeV1.LIVEKIT_CONTROL_API:
            _require(self.runtime_instance_ref is None, "route_native_runtime_instance_forbidden")
        else:
            _require(_valid_identifier(self.runtime_instance_ref), "route_runtime_instance_required")
        _require(_valid_identifier(self.publication_ref), "route_publication_ref_invalid")
        _require(isinstance(self.media_kind, MediaKindV1), "route_media_kind_invalid")
        _require(
            isinstance(self.allowed_layers, tuple)
            and 1 <= len(self.allowed_layers) <= MAX_ROUTE_LAYERS_V1
            and len(set(self.allowed_layers)) == len(self.allowed_layers),
            "route_layers_invalid",
        )
        if self.media_kind is MediaKindV1.AUDIO:
            _require(
                all(layer.rid is None and layer.spatial_id == 0 and layer.temporal_id == 0 for layer in self.allowed_layers),
                "route_audio_layer_invalid",
            )
        else:
            _require(all(layer.rid is not None for layer in self.allowed_layers), "route_video_layer_invalid")
        _require(
            isinstance(self.traffic_budgets, tuple)
            and 1 <= len(self.traffic_budgets) <= MAX_ROUTE_TRAFFIC_CLASSES_V1
            and len({budget.traffic_class for budget in self.traffic_budgets}) == len(self.traffic_budgets),
            "route_traffic_budgets_invalid",
        )
        _require(
            _positive_int(self.max_total_bitrate_bps)
            and self.max_total_bitrate_bps <= MAX_ROUTE_BITRATE_BPS_V1
            and sum(budget.max_bitrate_bps for budget in self.traffic_budgets) <= self.max_total_bitrate_bps,
            "route_total_bitrate_invalid",
        )
        _require(_positive_int(self.issued_at_ms), "route_issued_at_invalid")
        _require(_positive_int(self.expires_at_ms), "route_expires_at_invalid")
        _require(
            self.issued_at_ms < self.expires_at_ms
            and self.expires_at_ms - self.issued_at_ms <= MAX_ROUTE_TTL_MS_V1,
            "route_ttl_invalid",
        )
        _require(isinstance(self.version, RouteVersionV1), "route_version_invalid")
        _require(
            self.audience_projection_version == self.version.projection_version,
            "route_projection_version_mismatch",
        )
        _require(
            isinstance(self.intent_digest, str) and _DIGEST_HEX.fullmatch(self.intent_digest) is not None,
            "route_intent_digest_invalid",
        )

    @property
    def ttl_ms(self) -> int:
        return self.expires_at_ms - self.issued_at_ms


def _require_successor(candidate: RouteVersionV1, predecessor: RouteVersionV1) -> None:
    _require(candidate.route_epoch > predecessor.route_epoch, "route_epoch_not_advanced")
    _require(candidate.projection_version > predecessor.projection_version, "route_projection_version_not_advanced")
    _require(candidate.topology_epoch >= predecessor.topology_epoch, "route_topology_epoch_regressed")
    _require(candidate.key_epoch >= predecessor.key_epoch, "route_key_epoch_regressed")
    _require(candidate.fencing_token != predecessor.fencing_token, "route_fencing_token_not_rotated")


@dataclass(frozen=True, slots=True)
class ApplyRouteCommandV1:
    operation_id: str
    desired: RouteProjectionV1

    contract_version: ClassVar[str] = ROUTE_PORT_CONTRACT_V1

    def __post_init__(self) -> None:
        _require(_valid_identifier(self.operation_id), "route_operation_id_invalid")
        _require(isinstance(self.desired, RouteProjectionV1), "route_projection_invalid")


@dataclass(frozen=True, slots=True)
class UpdateRouteCommandV1:
    operation_id: str
    expected_version: RouteVersionV1
    desired: RouteProjectionV1

    contract_version: ClassVar[str] = ROUTE_PORT_CONTRACT_V1

    def __post_init__(self) -> None:
        _require(_valid_identifier(self.operation_id), "route_operation_id_invalid")
        _require(isinstance(self.expected_version, RouteVersionV1), "route_expected_version_invalid")
        _require(isinstance(self.desired, RouteProjectionV1), "route_projection_invalid")
        _require_successor(self.desired.version, self.expected_version)


@dataclass(frozen=True, slots=True)
class RevokeRouteCommandV1:
    operation_id: str
    key: RouteKeyV1
    expected_version: RouteVersionV1
    revoke_version: RouteVersionV1
    requested_at_ms: int

    contract_version: ClassVar[str] = ROUTE_PORT_CONTRACT_V1

    def __post_init__(self) -> None:
        _require(_valid_identifier(self.operation_id), "route_operation_id_invalid")
        _require(isinstance(self.key, RouteKeyV1), "route_key_invalid")
        _require(isinstance(self.expected_version, RouteVersionV1), "route_expected_version_invalid")
        _require(isinstance(self.revoke_version, RouteVersionV1), "route_revoke_version_invalid")
        _require(_positive_int(self.requested_at_ms), "route_revoke_timestamp_invalid")
        _require_successor(self.revoke_version, self.expected_version)


@dataclass(frozen=True, slots=True)
class ObserveRouteQueryV1:
    key: RouteKeyV1

    contract_version: ClassVar[str] = ROUTE_PORT_CONTRACT_V1

    def __post_init__(self) -> None:
        _require(isinstance(self.key, RouteKeyV1), "route_key_invalid")


@dataclass(frozen=True, slots=True)
class RouteMutationResultV1:
    operation: RouteOperationV1
    operation_id: str
    key: RouteKeyV1
    outcome: RouteOutcomeV1
    reason_code: RouteReasonCodeV1
    observed_version: RouteVersionV1 | None
    occurred_at_ms: int
    retryable: bool

    contract_version: ClassVar[str] = ROUTE_PORT_CONTRACT_V1

    @property
    def acknowledged(self) -> bool:
        return self.outcome is RouteOutcomeV1.ACKNOWLEDGED


@dataclass(frozen=True, slots=True)
class RouteObservationResultV1:
    key: RouteKeyV1
    presence: RoutePresenceV1
    reason_code: RouteReasonCodeV1
    projection: RouteProjectionV1 | None
    tombstone_version: RouteVersionV1 | None
    observed_at_ms: int
    retryable: bool

    contract_version: ClassVar[str] = ROUTE_PORT_CONTRACT_V1


@runtime_checkable
class ApplyRoutePortV1(Protocol):
    def apply(self, command: ApplyRouteCommandV1) -> RouteMutationResultV1:
        """Apply one absolute projection if the route is absent."""


@runtime_checkable
class UpdateRoutePortV1(Protocol):
    def update(self, command: UpdateRouteCommandV1) -> RouteMutationResultV1:
        """Atomically replace an active route after exact Hub-fence matching."""


@runtime_checkable
class RevokeRoutePortV1(Protocol):
    def revoke(self, command: RevokeRouteCommandV1) -> RouteMutationResultV1:
        """Atomically remove an active route and retain its Hub tombstone."""


@runtime_checkable
class ObserveRoutePortV1(Protocol):
    def observe(self, query: ObserveRouteQueryV1) -> RouteObservationResultV1:
        """Observe only the exact runtime projection or its absence."""


__all__ = [
    "ApplyRouteCommandV1",
    "ApplyRoutePortV1",
    "MAX_ROUTE_BITRATE_BPS_V1",
    "MAX_ROUTE_BURST_BYTES_V1",
    "MAX_ROUTE_LAYERS_V1",
    "MAX_ROUTE_PACKETS_PER_SECOND_V1",
    "MAX_ROUTE_RECEIVERS_V1",
    "MAX_ROUTE_TRAFFIC_CLASSES_V1",
    "MAX_ROUTE_TTL_MS_V1",
    "MediaKindV1",
    "ObserveRoutePortV1",
    "ObserveRouteQueryV1",
    "ROUTE_PORT_CONTRACT_V1",
    "RevokeRouteCommandV1",
    "RevokeRoutePortV1",
    "RouteContractViolationV1",
    "RouteKeyV1",
    "RouteLayerV1",
    "RouteMutationResultV1",
    "RouteObservationResultV1",
    "RouteOperationV1",
    "RouteOutcomeV1",
    "RoutePresenceV1",
    "RouteProjectionV1",
    "RouteReasonCodeV1",
    "RouteTrafficBudgetV1",
    "RouteVersionV1",
    "RuntimeControlModeV1",
    "UpdateRouteCommandV1",
    "UpdateRoutePortV1",
]
