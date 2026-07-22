"""Vendor-neutral, evidence-bound capability boundary for SFU broadcast paths.

The Hub owns the support decision.  Adapters may translate a BASE-006
document into these immutable values, but vendor SDK objects, exceptions and
credentials never become part of the domain contract.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


BASE006_GATE_ID = "SFB-BASE-006"
BASE006_SCHEMA = "ananta.livekit-broadcast-runtime-capabilities.v1"
BASE006_ARTIFACT_PATH = "artifacts/domain/livekit-broadcast-runtime-capabilities.json"


class CapabilityStatus(str, Enum):
    """Stable public support states; no vendor-specific state escapes."""

    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"


class CapabilityKind(str, Enum):
    CODEC = "codec"
    SIMULCAST = "simulcast"
    SVC = "svc"
    ENCODED_TRANSFORM = "encoded_transform"
    SERVER_SUBSCRIPTION = "server_subscription"
    DATA_PACKET = "data_packet"
    DATA_STREAM = "data_stream"
    QUEUE_HOOK = "queue_hook"
    METRICS = "metrics"
    TURN = "turn"
    DRAIN = "drain"


class RuntimeComponent(str, Enum):
    LIVEKIT_SERVER = "sfu_server"
    BROWSER_SDK = "browser_sdk"


class CapabilityReasonCode(str, Enum):
    ARTIFACT_INVALID = "sfu_broadcast.capability.artifact_invalid"
    BASE_GATE_BLOCKED = "sfu_broadcast.capability.base_gate_blocked"
    CAPABILITY_MISSING = "sfu_broadcast.capability.missing"
    CAPABILITY_DEGRADED = "sfu_broadcast.capability.declared_degraded"
    CAPABILITY_UNSUPPORTED = "sfu_broadcast.capability.declared_unsupported"
    DOCUMENTATION_ONLY = "sfu_broadcast.capability.documentation_only"
    COMBINED_EVIDENCE_ONLY = "sfu_broadcast.capability.combined_evidence_only"
    STATUS_INVALID = "sfu_broadcast.capability.status_invalid"
    EVIDENCE_UNVERIFIED = "sfu_broadcast.capability.evidence_unverified"
    VERSION_UNVERIFIED = "sfu_broadcast.capability.version_unverified"
    REQUIRED_FACT_MISSING = "sfu_broadcast.capability.required_fact_missing"
    FEATURE_NOT_REQUESTED = "sfu_broadcast.capability.feature_not_requested"
    FEATURE_UNKNOWN = "sfu_broadcast.capability.feature_unknown"
    ADAPTER_REQUIREMENTS_MISSING = "sfu_broadcast.capability.adapter_requirements_missing"


_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_EVIDENCE_ID = re.compile(r"^(?:SRC|RUN)_[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SVC_MODE = re.compile(r"^L[1-3]T[1-3](?:_KEY)?$")


def _version_tuple(value: str | None) -> tuple[int, int, int] | None:
    match = _SEMVER.fullmatch(value or "")
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class VersionBoundary:
    component: RuntimeComponent
    minimum_inclusive: str | None
    maximum_inclusive: str | None
    observed_version: str | None

    @property
    def satisfied(self) -> bool:
        minimum = _version_tuple(self.minimum_inclusive)
        maximum = _version_tuple(self.maximum_inclusive)
        observed = _version_tuple(self.observed_version)
        return (
            minimum is not None
            and maximum is not None
            and observed is not None
            and minimum <= observed <= maximum
        )


@dataclass(frozen=True, slots=True)
class CapabilityEvidenceReference:
    """Reference to BASE-006, not a replacement for its real evidence."""

    gate_id: str
    artifact_schema: str
    artifact_path: str
    artifact_capability_id: str
    source_binding_sha256: str | None = None
    source_ids: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        identifiers = self.source_ids + self.run_ids
        if any(_EVIDENCE_ID.fullmatch(identifier) is None for identifier in identifiers):
            raise ValueError("sfu_broadcast.capability.evidence_identifier_invalid")
        if any(not identifier.startswith("SRC_") for identifier in self.source_ids):
            raise ValueError("sfu_broadcast.capability.source_identifier_invalid")
        if any(not identifier.startswith("RUN_") for identifier in self.run_ids):
            raise ValueError("sfu_broadcast.capability.run_identifier_invalid")
        if self.source_binding_sha256 is not None and _SHA256.fullmatch(self.source_binding_sha256) is None:
            raise ValueError("sfu_broadcast.capability.source_binding_invalid")

    @property
    def grounded(self) -> bool:
        return bool(self.source_ids or self.run_ids)


@dataclass(frozen=True, slots=True)
class CapabilitySupport:
    kind: CapabilityKind
    status: CapabilityStatus
    versions: tuple[VersionBoundary, ...]
    evidence: tuple[CapabilityEvidenceReference, ...]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status is CapabilityStatus.AVAILABLE and (
            not self.versions
            or not all(boundary.satisfied for boundary in self.versions)
            or not any(reference.grounded for reference in self.evidence)
        ):
            raise ValueError("sfu_broadcast.capability.available_without_grounded_versioned_evidence")

    @property
    def available(self) -> bool:
        return self.status is CapabilityStatus.AVAILABLE


@dataclass(frozen=True, slots=True)
class CodecCapability:
    support: CapabilitySupport
    codecs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SimulcastCapability:
    support: CapabilitySupport
    max_spatial_layers: int | None = None


@dataclass(frozen=True, slots=True)
class SvcCapability:
    support: CapabilitySupport
    scalability_modes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EncodedTransformCapability:
    support: CapabilitySupport
    e2ee_compatible: bool = False


@dataclass(frozen=True, slots=True)
class ServerSubscriptionCapability:
    support: CapabilitySupport
    server_authoritative: bool = False


@dataclass(frozen=True, slots=True)
class DataPacketCapability:
    support: CapabilitySupport
    reliable_payload_bytes: int | None = None
    lossy_payload_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class DataStreamCapability:
    support: CapabilitySupport


@dataclass(frozen=True, slots=True)
class QueueHookCapability:
    support: CapabilitySupport
    authenticated: bool = False
    fenced: bool = False


@dataclass(frozen=True, slots=True)
class MetricsCapability:
    support: CapabilitySupport
    egress_metrics: bool = False


@dataclass(frozen=True, slots=True)
class TurnCapability:
    support: CapabilitySupport
    embedded: bool = False


@dataclass(frozen=True, slots=True)
class DrainCapability:
    support: CapabilitySupport
    native: bool = False


@dataclass(frozen=True, slots=True)
class SfuBroadcastCapabilitySnapshot:
    codec: CodecCapability
    simulcast: SimulcastCapability
    svc: SvcCapability
    encoded_transform: EncodedTransformCapability
    server_subscription: ServerSubscriptionCapability
    data_packet: DataPacketCapability
    data_stream: DataStreamCapability
    queue_hook: QueueHookCapability
    metrics: MetricsCapability
    turn: TurnCapability
    drain: DrainCapability

    def support_for(self, kind: CapabilityKind) -> CapabilitySupport:
        values = {
            CapabilityKind.CODEC: self.codec.support,
            CapabilityKind.SIMULCAST: self.simulcast.support,
            CapabilityKind.SVC: self.svc.support,
            CapabilityKind.ENCODED_TRANSFORM: self.encoded_transform.support,
            CapabilityKind.SERVER_SUBSCRIPTION: self.server_subscription.support,
            CapabilityKind.DATA_PACKET: self.data_packet.support,
            CapabilityKind.DATA_STREAM: self.data_stream.support,
            CapabilityKind.QUEUE_HOOK: self.queue_hook.support,
            CapabilityKind.METRICS: self.metrics.support,
            CapabilityKind.TURN: self.turn.support,
            CapabilityKind.DRAIN: self.drain.support,
        }
        return values[kind]


class CodecCapabilityPort(Protocol):
    def codec_capability(self) -> CodecCapability: ...


class SimulcastCapabilityPort(Protocol):
    def simulcast_capability(self) -> SimulcastCapability: ...


class SvcCapabilityPort(Protocol):
    def svc_capability(self) -> SvcCapability: ...


class EncodedTransformCapabilityPort(Protocol):
    def encoded_transform_capability(self) -> EncodedTransformCapability: ...


class ServerSubscriptionCapabilityPort(Protocol):
    def server_subscription_capability(self) -> ServerSubscriptionCapability: ...


class DataPacketCapabilityPort(Protocol):
    def data_packet_capability(self) -> DataPacketCapability: ...


class DataStreamCapabilityPort(Protocol):
    def data_stream_capability(self) -> DataStreamCapability: ...


class QueueHookCapabilityPort(Protocol):
    def queue_hook_capability(self) -> QueueHookCapability: ...


class MetricsCapabilityPort(Protocol):
    def metrics_capability(self) -> MetricsCapability: ...


class TurnCapabilityPort(Protocol):
    def turn_capability(self) -> TurnCapability: ...


class DrainCapabilityPort(Protocol):
    def drain_capability(self) -> DrainCapability: ...


class SfuBroadcastCapabilitySnapshotPort(Protocol):
    def capability_snapshot(self) -> SfuBroadcastCapabilitySnapshot: ...


_ROW_KEYS: Mapping[CapabilityKind, tuple[tuple[str, bool], ...]] = {
    CapabilityKind.CODEC: (("codec", False),),
    CapabilityKind.SIMULCAST: (("simulcast", False), ("simulcast_svc_track_publish_options", True)),
    CapabilityKind.SVC: (("svc_mode", False), ("simulcast_svc_track_publish_options", True)),
    CapabilityKind.ENCODED_TRANSFORM: (("encoded_transform_compatibility", False),),
    CapabilityKind.SERVER_SUBSCRIPTION: (
        ("server_subscription_control", False),
        ("room_service_update_subscriptions", False),
    ),
    CapabilityKind.DATA_PACKET: (("data_packet", False), ("data_packet_limits", False)),
    CapabilityKind.DATA_STREAM: (("data_stream", False),),
    CapabilityKind.QUEUE_HOOK: (("queue_hook", False), ("runtime_route_epoch_queue_fencing", True)),
    CapabilityKind.METRICS: (("egress_metrics", False), ("prometheus_metrics", True)),
    CapabilityKind.TURN: (("turn", False), ("embedded_turn", False)),
    CapabilityKind.DRAIN: (("drain", False), ("native_drain", False)),
}

_CLIENT_CAPABILITIES = frozenset(
    {CapabilityKind.CODEC, CapabilityKind.SIMULCAST, CapabilityKind.SVC, CapabilityKind.ENCODED_TRANSFORM}
)
_DUAL_CAPABILITIES = frozenset({CapabilityKind.DATA_PACKET, CapabilityKind.DATA_STREAM})


class Base006BroadcastCapabilityAdapter:
    """Translate an already-read BASE-006 primitive document, failing closed."""

    __slots__ = ("_snapshot",)

    def __init__(
        self,
        document: Mapping[str, object],
        *,
        provided_evidence_ids: Iterable[str] = (),
    ) -> None:
        try:
            provided = frozenset(
                identifier
                for identifier in provided_evidence_ids
                if isinstance(identifier, str) and _EVIDENCE_ID.fullmatch(identifier) is not None
            )
            self._snapshot = _snapshot_from_document(document, provided)
        except Exception:
            # The boundary absorbs parser/SDK-shaped failures and exports only
            # a stable unsupported domain snapshot.
            self._snapshot = _unsupported_snapshot(CapabilityReasonCode.ARTIFACT_INVALID.value)

    def capability_snapshot(self) -> SfuBroadcastCapabilitySnapshot:
        return self._snapshot

    def codec_capability(self) -> CodecCapability:
        return self._snapshot.codec

    def simulcast_capability(self) -> SimulcastCapability:
        return self._snapshot.simulcast

    def svc_capability(self) -> SvcCapability:
        return self._snapshot.svc

    def encoded_transform_capability(self) -> EncodedTransformCapability:
        return self._snapshot.encoded_transform

    def server_subscription_capability(self) -> ServerSubscriptionCapability:
        return self._snapshot.server_subscription

    def data_packet_capability(self) -> DataPacketCapability:
        return self._snapshot.data_packet

    def data_stream_capability(self) -> DataStreamCapability:
        return self._snapshot.data_stream

    def queue_hook_capability(self) -> QueueHookCapability:
        return self._snapshot.queue_hook

    def metrics_capability(self) -> MetricsCapability:
        return self._snapshot.metrics

    def turn_capability(self) -> TurnCapability:
        return self._snapshot.turn

    def drain_capability(self) -> DrainCapability:
        return self._snapshot.drain


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _rows(document: Mapping[str, object]) -> tuple[dict[str, Mapping[str, object]], frozenset[str]]:
    indexed: dict[str, Mapping[str, object]] = {}
    duplicates: set[str] = set()
    raw_rows = document.get("capabilities")
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes, bytearray)):
        return indexed, frozenset()
    for candidate in raw_rows:
        row = _as_mapping(candidate)
        identifier = row.get("capability")
        if not isinstance(identifier, str) or not identifier:
            continue
        if identifier in indexed:
            duplicates.add(identifier)
        else:
            indexed[identifier] = row
    return indexed, frozenset(duplicates)


def _select_row(
    kind: CapabilityKind,
    indexed: Mapping[str, Mapping[str, object]],
    duplicates: frozenset[str],
) -> tuple[Mapping[str, object] | None, str, bool, bool]:
    for identifier, is_combined in _ROW_KEYS[kind]:
        if identifier in indexed:
            return indexed[identifier], identifier, is_combined, identifier in duplicates
    return None, _ROW_KEYS[kind][0][0], False, False


def _boundary(document: Mapping[str, object], component: RuntimeComponent) -> VersionBoundary:
    version_binding = _as_mapping(document.get("version_binding"))
    if component is RuntimeComponent.BROWSER_SDK:
        browser = _as_mapping(version_binding.get("browser_sdk"))
        expected = browser.get("expected_version")
        package = browser.get("package_version")
        lock = browser.get("lock_version")
        observed = package if isinstance(package, str) and package == lock else None
    else:
        server = _as_mapping(version_binding.get("server"))
        expected = server.get("expected_version")
        observed = server.get("runtime_version")
    expected_value = expected if isinstance(expected, str) else None
    observed_value = observed if isinstance(observed, str) else None
    return VersionBoundary(component, expected_value, expected_value, observed_value)


def _boundaries(document: Mapping[str, object], kind: CapabilityKind) -> tuple[VersionBoundary, ...]:
    if kind in _CLIENT_CAPABILITIES:
        return (_boundary(document, RuntimeComponent.BROWSER_SDK),)
    if kind in _DUAL_CAPABILITIES:
        return (
            _boundary(document, RuntimeComponent.LIVEKIT_SERVER),
            _boundary(document, RuntimeComponent.BROWSER_SDK),
        )
    return (_boundary(document, RuntimeComponent.LIVEKIT_SERVER),)


def _identifier_candidates(row: Mapping[str, object]) -> frozenset[str]:
    found: set[str] = set()
    containers: list[Mapping[str, object]] = [row]
    evidence = row.get("evidence")
    if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes, bytearray)):
        containers.extend(_as_mapping(item) for item in evidence)
    for container in containers:
        for singular in ("source_id", "run_id"):
            value = container.get(singular)
            if isinstance(value, str) and _EVIDENCE_ID.fullmatch(value) is not None:
                found.add(value)
        for plural in ("source_ids", "run_ids"):
            values = container.get(plural)
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
                found.update(
                    value
                    for value in values
                    if isinstance(value, str) and _EVIDENCE_ID.fullmatch(value) is not None
                )
    return frozenset(found)


def _reference(
    document: Mapping[str, object],
    row: Mapping[str, object] | None,
    capability_id: str,
    provided: frozenset[str],
) -> CapabilityEvidenceReference:
    binding = _as_mapping(document.get("version_binding")).get("source_sha256")
    binding_value = binding if isinstance(binding, str) and _SHA256.fullmatch(binding) is not None else None
    accepted = _identifier_candidates(row or {}) & provided
    return CapabilityEvidenceReference(
        gate_id=BASE006_GATE_ID,
        artifact_schema=BASE006_SCHEMA,
        artifact_path=BASE006_ARTIFACT_PATH,
        artifact_capability_id=capability_id,
        source_binding_sha256=binding_value,
        source_ids=tuple(sorted(identifier for identifier in accepted if identifier.startswith("SRC_"))),
        run_ids=tuple(sorted(identifier for identifier in accepted if identifier.startswith("RUN_"))),
    )


def _codec_values(row: Mapping[str, object] | None) -> tuple[str, ...]:
    allowed = {"av1", "h264", "opus", "vp8", "vp9"}
    values = (row or {}).get("codecs")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return ()
    return tuple(sorted({value.casefold() for value in values if isinstance(value, str) and value.casefold() in allowed}))


def _svc_modes(row: Mapping[str, object] | None) -> tuple[str, ...]:
    values = (row or {}).get("scalability_modes")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return ()
    return tuple(sorted({value for value in values if isinstance(value, str) and _SVC_MODE.fullmatch(value)}))


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _facts_valid(kind: CapabilityKind, row: Mapping[str, object]) -> bool:
    if kind is CapabilityKind.CODEC:
        return bool(_codec_values(row))
    if kind is CapabilityKind.SVC:
        return bool(_svc_modes(row))
    if kind is CapabilityKind.ENCODED_TRANSFORM:
        return row.get("e2ee_compatible") is True
    if kind is CapabilityKind.DATA_PACKET:
        limits = _as_mapping(row.get("limits"))
        return _positive_int(limits.get("reliable_payload_bytes")) is not None and _positive_int(
            limits.get("lossy_payload_bytes")
        ) is not None
    if kind is CapabilityKind.QUEUE_HOOK:
        return row.get("authenticated") is True and row.get("fenced") is True
    return True


def _support(
    document: Mapping[str, object],
    kind: CapabilityKind,
    row: Mapping[str, object] | None,
    capability_id: str,
    combined: bool,
    duplicate: bool,
    provided: frozenset[str],
    artifact_valid: bool,
) -> CapabilitySupport:
    versions = _boundaries(document, kind)
    reference = _reference(document, row, capability_id, provided)
    reasons: set[str] = set()
    status = CapabilityStatus.UNSUPPORTED
    if not artifact_valid or duplicate:
        reasons.add(CapabilityReasonCode.ARTIFACT_INVALID.value)
    elif row is None:
        reasons.add(CapabilityReasonCode.CAPABILITY_MISSING.value)
    else:
        raw_status = row.get("status")
        if raw_status == CapabilityStatus.UNSUPPORTED.value:
            reasons.add(CapabilityReasonCode.CAPABILITY_UNSUPPORTED.value)
        elif raw_status in {CapabilityStatus.DEGRADED.value, "documented"}:
            status = CapabilityStatus.DEGRADED
            reasons.add(
                CapabilityReasonCode.DOCUMENTATION_ONLY.value
                if raw_status == "documented"
                else CapabilityReasonCode.CAPABILITY_DEGRADED.value
            )
        elif raw_status == CapabilityStatus.AVAILABLE.value:
            status = CapabilityStatus.AVAILABLE
        else:
            reasons.add(CapabilityReasonCode.STATUS_INVALID.value)

        if status is CapabilityStatus.AVAILABLE and document.get("decision") != "go":
            status = CapabilityStatus.DEGRADED
            reasons.add(CapabilityReasonCode.BASE_GATE_BLOCKED.value)
        if status is CapabilityStatus.AVAILABLE and combined:
            status = CapabilityStatus.DEGRADED
            reasons.add(CapabilityReasonCode.COMBINED_EVIDENCE_ONLY.value)
        if status is CapabilityStatus.AVAILABLE and not all(boundary.satisfied for boundary in versions):
            status = CapabilityStatus.DEGRADED
            reasons.add(CapabilityReasonCode.VERSION_UNVERIFIED.value)
        if status is CapabilityStatus.AVAILABLE and not reference.grounded:
            status = CapabilityStatus.DEGRADED
            reasons.add(CapabilityReasonCode.EVIDENCE_UNVERIFIED.value)
        if status is CapabilityStatus.AVAILABLE and not _facts_valid(kind, row):
            status = CapabilityStatus.DEGRADED
            reasons.add(CapabilityReasonCode.REQUIRED_FACT_MISSING.value)
        if combined and status is CapabilityStatus.DEGRADED:
            reasons.add(CapabilityReasonCode.COMBINED_EVIDENCE_ONLY.value)

    return CapabilitySupport(kind, status, versions, (reference,), tuple(sorted(reasons)))


def _snapshot_from_document(
    document: Mapping[str, object],
    provided: frozenset[str],
) -> SfuBroadcastCapabilitySnapshot:
    artifact_valid = document.get("schema") == BASE006_SCHEMA and document.get("gate_id") == BASE006_GATE_ID
    indexed, duplicates = _rows(document)
    supports: dict[CapabilityKind, CapabilitySupport] = {}
    selected: dict[CapabilityKind, Mapping[str, object] | None] = {}
    for kind in CapabilityKind:
        row, capability_id, combined, duplicate = _select_row(kind, indexed, duplicates)
        selected[kind] = row
        supports[kind] = _support(
            document, kind, row, capability_id, combined, duplicate, provided, artifact_valid
        )

    packet_limits = _as_mapping((selected[CapabilityKind.DATA_PACKET] or {}).get("limits"))
    simulcast_layers = _positive_int((selected[CapabilityKind.SIMULCAST] or {}).get("max_spatial_layers"))
    return SfuBroadcastCapabilitySnapshot(
        codec=CodecCapability(supports[CapabilityKind.CODEC], _codec_values(selected[CapabilityKind.CODEC])),
        simulcast=SimulcastCapability(supports[CapabilityKind.SIMULCAST], simulcast_layers),
        svc=SvcCapability(supports[CapabilityKind.SVC], _svc_modes(selected[CapabilityKind.SVC])),
        encoded_transform=EncodedTransformCapability(
            supports[CapabilityKind.ENCODED_TRANSFORM],
            supports[CapabilityKind.ENCODED_TRANSFORM].available
            and (selected[CapabilityKind.ENCODED_TRANSFORM] or {}).get("e2ee_compatible") is True,
        ),
        server_subscription=ServerSubscriptionCapability(
            supports[CapabilityKind.SERVER_SUBSCRIPTION], supports[CapabilityKind.SERVER_SUBSCRIPTION].available
        ),
        data_packet=DataPacketCapability(
            supports[CapabilityKind.DATA_PACKET],
            _positive_int(packet_limits.get("reliable_payload_bytes")),
            _positive_int(packet_limits.get("lossy_payload_bytes")),
        ),
        data_stream=DataStreamCapability(supports[CapabilityKind.DATA_STREAM]),
        queue_hook=QueueHookCapability(
            supports[CapabilityKind.QUEUE_HOOK],
            supports[CapabilityKind.QUEUE_HOOK].available
            and (selected[CapabilityKind.QUEUE_HOOK] or {}).get("authenticated") is True,
            supports[CapabilityKind.QUEUE_HOOK].available
            and (selected[CapabilityKind.QUEUE_HOOK] or {}).get("fenced") is True,
        ),
        metrics=MetricsCapability(
            supports[CapabilityKind.METRICS], supports[CapabilityKind.METRICS].available
        ),
        turn=TurnCapability(supports[CapabilityKind.TURN], supports[CapabilityKind.TURN].available),
        drain=DrainCapability(supports[CapabilityKind.DRAIN], supports[CapabilityKind.DRAIN].available),
    )


def _unsupported_snapshot(reason_code: str) -> SfuBroadcastCapabilitySnapshot:
    document: Mapping[str, object] = {"schema": "invalid", "gate_id": "invalid", "capabilities": []}
    snapshot = _snapshot_from_document(document, frozenset())
    # The parser already supplies the same stable invalid-artifact reason.  The
    # parameter keeps the boundary explicit for future adapter failure classes.
    assert reason_code == CapabilityReasonCode.ARTIFACT_INVALID.value
    return snapshot


@dataclass(frozen=True, slots=True)
class FeatureCapabilityRequirement:
    feature_key: str
    capabilities: tuple[CapabilityKind, ...]


SFB_FEATURE_CAPABILITY_REQUIREMENTS: tuple[FeatureCapabilityRequirement, ...] = (
    FeatureCapabilityRequirement(
        "semantic_media_broadcast",
        (CapabilityKind.CODEC, CapabilityKind.ENCODED_TRANSFORM, CapabilityKind.SERVER_SUBSCRIPTION),
    ),
    FeatureCapabilityRequirement("semantic_media_receiver_groups", (CapabilityKind.SERVER_SUBSCRIPTION,)),
    FeatureCapabilityRequirement(
        "semantic_media_fleet_admission", (CapabilityKind.METRICS, CapabilityKind.DRAIN)
    ),
    FeatureCapabilityRequirement(
        "semantic_media_turn_cost_controls", (CapabilityKind.TURN, CapabilityKind.METRICS)
    ),
    FeatureCapabilityRequirement(
        "semantic_media_simulcast", (CapabilityKind.CODEC, CapabilityKind.SIMULCAST, CapabilityKind.ENCODED_TRANSFORM)
    ),
    FeatureCapabilityRequirement(
        "semantic_media_svc", (CapabilityKind.CODEC, CapabilityKind.SVC, CapabilityKind.ENCODED_TRANSFORM)
    ),
    FeatureCapabilityRequirement("semantic_media_data_fanout", (CapabilityKind.DATA_PACKET,)),
    FeatureCapabilityRequirement("semantic_media_data_stream", (CapabilityKind.DATA_STREAM,)),
    FeatureCapabilityRequirement("semantic_media_runtime_queue_hook", (CapabilityKind.QUEUE_HOOK,)),
)


@dataclass(frozen=True, slots=True)
class AdapterPathRequirement:
    path_id: str
    capabilities: tuple[CapabilityKind, ...]
    carries_media: bool = False


@dataclass(frozen=True, slots=True)
class CapabilityGateDecision:
    allowed: bool
    reason_codes: tuple[str, ...]
    required_capabilities: tuple[CapabilityKind, ...]
    blocking_capabilities: tuple[CapabilityKind, ...]

    @property
    def flag_enabled(self) -> bool:
        return self.allowed

    @property
    def adapter_allowed(self) -> bool:
        return self.allowed


class CapabilitySupportGate:
    """One fail-closed policy shared by Hub flags and adapter entry points."""

    def __init__(self, capability_port: SfuBroadcastCapabilitySnapshotPort) -> None:
        self._capability_port = capability_port

    def feature_flag_decision(self, feature_key: str, requested: object) -> CapabilityGateDecision:
        requirement = next(
            (item for item in SFB_FEATURE_CAPABILITY_REQUIREMENTS if item.feature_key == feature_key),
            None,
        )
        if requirement is None:
            return CapabilityGateDecision(
                False, (CapabilityReasonCode.FEATURE_UNKNOWN.value,), (), ()
            )
        if requested is not True:
            return CapabilityGateDecision(
                False,
                (CapabilityReasonCode.FEATURE_NOT_REQUESTED.value,),
                requirement.capabilities,
                (),
            )
        return self._decide(requirement.capabilities)

    def resolve_feature_flags(self, requested: Mapping[str, object]) -> dict[str, bool]:
        return {
            requirement.feature_key: self.feature_flag_decision(
                requirement.feature_key, requested.get(requirement.feature_key)
            ).allowed
            for requirement in SFB_FEATURE_CAPABILITY_REQUIREMENTS
        }

    def adapter_path_decision(self, requirement: AdapterPathRequirement) -> CapabilityGateDecision:
        required = list(requirement.capabilities)
        if requirement.carries_media and CapabilityKind.ENCODED_TRANSFORM not in required:
            required.append(CapabilityKind.ENCODED_TRANSFORM)
        if not required:
            return CapabilityGateDecision(
                False,
                (CapabilityReasonCode.ADAPTER_REQUIREMENTS_MISSING.value,),
                (),
                (),
            )
        return self._decide(tuple(dict.fromkeys(required)))

    def _decide(self, required: tuple[CapabilityKind, ...]) -> CapabilityGateDecision:
        snapshot = self._capability_port.capability_snapshot()
        blockers: list[CapabilityKind] = []
        reasons: set[str] = set()
        for kind in required:
            support = snapshot.support_for(kind)
            if not support.available:
                blockers.append(kind)
                reasons.add(f"sfu_broadcast.capability.{kind.value}.{support.status.value}")
                reasons.update(support.reason_codes)
        return CapabilityGateDecision(
            not blockers,
            tuple(sorted(reasons)),
            required,
            tuple(blockers),
        )


__all__ = [
    "AdapterPathRequirement",
    "BASE006_ARTIFACT_PATH",
    "BASE006_GATE_ID",
    "BASE006_SCHEMA",
    "Base006BroadcastCapabilityAdapter",
    "CapabilityEvidenceReference",
    "CapabilityGateDecision",
    "CapabilityKind",
    "CapabilityReasonCode",
    "CapabilityStatus",
    "CapabilitySupport",
    "CapabilitySupportGate",
    "CodecCapability",
    "CodecCapabilityPort",
    "DataPacketCapability",
    "DataPacketCapabilityPort",
    "DataStreamCapability",
    "DataStreamCapabilityPort",
    "DrainCapability",
    "DrainCapabilityPort",
    "EncodedTransformCapability",
    "EncodedTransformCapabilityPort",
    "FeatureCapabilityRequirement",
    "MetricsCapability",
    "MetricsCapabilityPort",
    "QueueHookCapability",
    "QueueHookCapabilityPort",
    "RuntimeComponent",
    "SFB_FEATURE_CAPABILITY_REQUIREMENTS",
    "ServerSubscriptionCapability",
    "ServerSubscriptionCapabilityPort",
    "SfuBroadcastCapabilitySnapshot",
    "SfuBroadcastCapabilitySnapshotPort",
    "SimulcastCapability",
    "SimulcastCapabilityPort",
    "SvcCapability",
    "SvcCapabilityPort",
    "TurnCapability",
    "TurnCapabilityPort",
    "VersionBoundary",
]
