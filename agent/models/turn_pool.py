"""Registered TURN pool nodes as persisted by the Hub pool directory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True)
class TurnPoolRegistration:
    pool_id: str
    instance_id: str
    region: str
    endpoints: tuple[Mapping[str, str], ...]
    credential_modes: tuple[str, ...]
    config_version: str
    config_digest: str
    observer_identity_id: str
    observer_identity_version: int
    trust_policy_version: str
    cost_units: int


@dataclass(frozen=True)
class TurnPoolNode:
    pool_id: str
    instance_id: str
    region: str
    endpoints: tuple[Mapping[str, str], ...]
    credential_modes: tuple[str, ...]
    config_version: str
    config_digest: str
    observer_identity_id: str
    observer_identity_version: int
    trust_policy_version: str
    lifecycle_state: str
    health_status: str
    relay_ready: bool
    capacity_status: str
    cost_units: int
    fresh_until: datetime | None
    observation_fencing_token: int
    version: int


__all__ = [
    "TurnPoolNode",
    "TurnPoolRegistration",
]
