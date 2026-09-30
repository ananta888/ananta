"""Admission scope of a signed SFU layer projection."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SfuProjectionScope:
    tenant_id: str
    room_id: str
    actor_id: str
    membership_epoch: int
    route_epoch: int = 0
    topology_epoch: int = 0
    key_epoch: int = 0


__all__ = [
    "SfuProjectionScope",
]
