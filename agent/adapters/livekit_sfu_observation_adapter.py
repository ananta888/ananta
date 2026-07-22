"""Bounded Hub-side observation job for ``livekit_control_api`` mode."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Mapping, Protocol


class LiveKitSfuObservationError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.retryable = retryable


class LiveKitObservationSourcePort(Protocol):
    def collect(self, *, timeout_ms: int, response_bytes_max: int) -> Mapping[str, object]: ...


class SfuObservationSequencePort(Protocol):
    def next_sequence(self, *, producer_id: str, boot_id: str) -> tuple[int, int]: ...


class SfuObservationSinkPort(Protocol):
    def submit(self, document: Mapping[str, object], *, deadline_ms: int) -> None: ...


@dataclass(frozen=True, slots=True)
class LiveKitSfuObservationConfig:
    producer_id: str
    boot_id: str
    tenant_id: str
    cluster_id: str
    region: str
    config_digest: str
    image_digest: str
    timeout_ms: int = 2_000
    response_bytes_max: int = 262_144
    payload_bytes_max: int = 65_536
    ttl_ms: int = 30_000
    retry_max: int = 2

    def __post_init__(self) -> None:
        for value in (self.producer_id, self.boot_id, self.tenant_id, self.cluster_id, self.region):
            if not value or len(value) > 128:
                raise ValueError("livekit_observation_scope_invalid")
        for digest in (self.config_digest, self.image_digest):
            if not digest.startswith("sha256:") or len(digest) != 71:
                raise ValueError("livekit_observation_digest_invalid")
        if not 100 <= self.timeout_ms <= 10_000 or not 4_096 <= self.payload_bytes_max <= 262_144:
            raise ValueError("livekit_observation_bound_invalid")
        if not 5_000 <= self.ttl_ms <= 60_000 or not 0 <= self.retry_max <= 5:
            raise ValueError("livekit_observation_bound_invalid")


class LiveKitSfuObservationAdapter:
    """One job slice; the Hub scheduler owns periodic execution and retries."""

    _SOURCE_FIELDS = frozenset(
        {"capabilities", "health", "capacity", "pressure", "observed_node_id"}
    )

    def __init__(
        self,
        *,
        source: LiveKitObservationSourcePort,
        sequences: SfuObservationSequencePort,
        sink: SfuObservationSinkPort,
        config: LiveKitSfuObservationConfig,
        clock_ms=lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._source = source
        self._sequences = sequences
        self._sink = sink
        self._config = config
        self._clock_ms = clock_ms

    def run_once(self) -> Mapping[str, object]:
        measured_at = self._clock_ms()
        try:
            observed = dict(
                self._source.collect(
                    timeout_ms=self._config.timeout_ms,
                    response_bytes_max=self._config.response_bytes_max,
                )
            )
        except Exception:
            observed = {
                "capabilities": [],
                "health": {
                    "liveness": None,
                    "control_ready": None,
                    "media_ready": None,
                    "admission_ready": None,
                },
                "capacity": {
                    "receiver_limit": None,
                    "room_limit": None,
                    "egress_bps": None,
                    "memory_bytes_limit": None,
                },
                "pressure": {
                    "cpu_ratio": None,
                    "memory_ratio": None,
                    "fd_ratio": None,
                    "udp_port_ratio": None,
                    "packet_drop_ratio": None,
                },
                "observed_node_id": None,
            }
        if set(observed) != self._SOURCE_FIELDS:
            raise LiveKitSfuObservationError("livekit_observation_source_fields_invalid")
        sequence, producer_fencing_token = self._sequences.next_sequence(
            producer_id=self._config.producer_id,
            boot_id=self._config.boot_id,
        )
        capabilities = list(observed["capabilities"])
        capability_digest = "sha256:" + hashlib.sha256(
            json.dumps(
                capabilities,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
        ).hexdigest()
        document = {
            "schema_version": "sfu_runtime_observation.v2",
            "producer_mode": "livekit_control_api",
            "scope": {
                "tenant_id": self._config.tenant_id,
                "cluster_id": self._config.cluster_id,
                "region": self._config.region,
                "observed_node_id": observed["observed_node_id"],
                "node_binding_authority": "non_authoritative_observation",
            },
            "producer_id": self._config.producer_id,
            "producer_fencing_token": producer_fencing_token,
            "boot_id": self._config.boot_id,
            "sequence": sequence,
            "measured_at_ms": measured_at,
            "valid_until_ms": measured_at + self._config.ttl_ms,
            "config_digest": self._config.config_digest,
            "image_digest": self._config.image_digest,
            "capability_digest": capability_digest,
            "capabilities": capabilities,
            "health": dict(observed["health"]),
            "capacity": dict(observed["capacity"]),
            "pressure": dict(observed["pressure"]),
            "labels": {"source": "livekit_server_api_metrics"},
            "proof": None,
        }
        encoded = json.dumps(document, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > self._config.payload_bytes_max:
            raise LiveKitSfuObservationError("livekit_observation_payload_oversize")
        self._sink.submit(document, deadline_ms=measured_at + self._config.timeout_ms)
        return document


__all__ = [
    "LiveKitObservationSourcePort",
    "LiveKitSfuObservationAdapter",
    "LiveKitSfuObservationConfig",
    "LiveKitSfuObservationError",
    "SfuObservationSequencePort",
    "SfuObservationSinkPort",
]
