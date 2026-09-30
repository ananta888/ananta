"""Value types of the tenant-scoped SFU broadcast operations read model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class SfuBroadcastOperationsError(ValueError):
    def __init__(self, reason_code: str, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuBroadcastOperationsRecord:
    observed_at_seconds: float
    tenant_ref: str
    region: str
    room_ref: str
    owner_subject: str
    receiver_ref: str
    cohort_size: int
    group_status: str
    route_status: str
    epoch_class: str
    topology: str
    health: str
    requested_layer: str
    allowed_layer: str
    effective_layer: str
    layer_distribution: Mapping[str, int]
    queue_depth: int
    drop_reason: str
    ingress_bytes_per_second: int
    egress_bytes_per_second: int
    turn_bytes_per_second: int
    rekey_status: str
    failover_status: str
    capacity_profile: str
    gate_state: str


@dataclass(frozen=True, slots=True)
class SfuBroadcastOperationsSnapshot:
    version: str
    records: tuple[SfuBroadcastOperationsRecord, ...]


@dataclass(frozen=True, slots=True)
class SfuBroadcastOperationsSourceScope:
    tenant_refs: tuple[str, ...] | None = None
    region_refs: tuple[str, ...] | None = None
    owner_subject: str | None = None
    room_ref: str | None = None
    receiver_ref: str | None = None


__all__ = [
    "SfuBroadcastOperationsError",
    "SfuBroadcastOperationsRecord",
    "SfuBroadcastOperationsSnapshot",
    "SfuBroadcastOperationsSourceScope",
]
