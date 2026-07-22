"""Canonical, versioned TURN pool control-plane documents.

The ingestion and directory paths deliberately share this module so that a
signed observer document cannot be accepted and then fail under a second,
incompatible vocabulary in the pool repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


TURN_POOL_CONTRACT_VERSION = 1
_CAPACITY = {"accept", "reduce", "stop"}
_HEALTH = {"healthy", "degraded", "unhealthy", "unknown"}
_RELAY = {"ready", "not_ready", "unknown"}


class TurnPoolContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TurnPoolNodeDocument:
    pool_id: str
    instance_id: str
    region: str
    endpoints: tuple[dict[str, Any], ...]
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
    cost_units: float
    fresh_until: Any
    observation_fencing_token: int
    version: int
    contract_version: int = TURN_POOL_CONTRACT_VERSION

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TurnPoolNodeDocument":
        endpoints = value.get("endpoints")
        modes = value.get("credential_modes")
        if not isinstance(endpoints, (list, tuple)) or not isinstance(modes, (list, tuple)):
            raise TurnPoolContractError("turn_pool_node_routes_invalid")
        document = cls(
            pool_id=_required(value, "pool_id"),
            instance_id=_required(value, "instance_id"),
            region=_required(value, "region"),
            endpoints=tuple(dict(item) for item in endpoints if isinstance(item, Mapping)),
            credential_modes=tuple(str(item) for item in modes),
            config_version=_required(value, "config_version"),
            config_digest=_required(value, "config_digest"),
            observer_identity_id=_required(value, "observer_identity_id"),
            observer_identity_version=_positive(value, "observer_identity_version"),
            trust_policy_version=_required(value, "trust_policy_version"),
            lifecycle_state=str(value.get("lifecycle_state", "stopped")),
            health_status=str(value.get("health_status", "unknown")),
            relay_ready=bool(value.get("relay_ready", False)),
            capacity_status=str(value.get("capacity_status", "stop")),
            cost_units=float(value.get("cost_units", 0.0)),
            fresh_until=value.get("fresh_until"),
            observation_fencing_token=int(value.get("observation_fencing_token", 0)),
            version=_positive(value, "version"),
            contract_version=int(value.get("contract_version", TURN_POOL_CONTRACT_VERSION)),
        )
        if (
            document.contract_version != TURN_POOL_CONTRACT_VERSION
            or not document.endpoints
            or not document.credential_modes
            or document.capacity_status not in _CAPACITY
            or document.health_status not in _HEALTH
            or document.lifecycle_state not in {"active", "draining", "stopped", "revoked"}
        ):
            raise TurnPoolContractError("turn_pool_node_document_invalid")
        return document

    def repository_mapping(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "pool_id": self.pool_id,
            "instance_id": self.instance_id,
            "region": self.region,
            "endpoints": [dict(item) for item in self.endpoints],
            "credential_modes": list(self.credential_modes),
            "config_version": self.config_version,
            "config_digest": self.config_digest,
            "observer_identity_id": self.observer_identity_id,
            "observer_identity_version": self.observer_identity_version,
            "trust_policy_version": self.trust_policy_version,
            "lifecycle_state": self.lifecycle_state,
            "health_status": self.health_status,
            "relay_ready": self.relay_ready,
            "capacity_status": self.capacity_status,
            "cost_units": self.cost_units,
            "fresh_until": self.fresh_until,
            "observation_fencing_token": self.observation_fencing_token,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class TurnPoolObservationDocument:
    node_id: str
    config_digest: str
    health_status: str
    relay_status: str
    capacity_status: str
    observation_fencing_token: int
    observation_version: int
    observed_at: float
    fresh_until: float
    contract_version: int = TURN_POOL_CONTRACT_VERSION

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TurnPoolObservationDocument":
        capacity = str(value.get("capacity_status", "unknown"))
        capacity = {"ready": "accept", "unknown": "stop"}.get(capacity, capacity)
        health = str(value.get("health_status", value.get("health", "unknown")))
        relay = str(value.get("relay_status", "unknown"))
        if "relay_ready" in value:
            relay = "ready" if bool(value["relay_ready"]) else "not_ready"
        document = cls(
            node_id=_required(value, "node_id"),
            config_digest=_required(value, "config_digest"),
            health_status=health,
            relay_status=relay,
            capacity_status=capacity,
            observation_fencing_token=_positive(value, "observation_fencing_token"),
            observation_version=_positive(value, "observation_version"),
            observed_at=_timestamp(value, "observed_at"),
            fresh_until=_timestamp(value, "fresh_until"),
            contract_version=int(value.get("contract_version", TURN_POOL_CONTRACT_VERSION)),
        )
        document.validate()
        return document

    def validate(self) -> None:
        if self.contract_version != TURN_POOL_CONTRACT_VERSION:
            raise TurnPoolContractError("turn_pool_contract_version_unsupported")
        if self.health_status not in _HEALTH:
            raise TurnPoolContractError("turn_pool_health_status_invalid")
        if self.relay_status not in _RELAY:
            raise TurnPoolContractError("turn_pool_relay_status_invalid")
        if self.capacity_status not in _CAPACITY:
            raise TurnPoolContractError("turn_pool_capacity_status_invalid")
        if self.fresh_until <= self.observed_at:
            raise TurnPoolContractError("turn_pool_freshness_window_invalid")

    def repository_mapping(self) -> dict[str, Any]:
        """Return the sole vocabulary consumed by the persistence adapter."""
        return {
            "contract_version": self.contract_version,
            "node_id": self.node_id,
            "config_digest": self.config_digest,
            "health_status": self.health_status,
            "relay_status": self.relay_status,
            "capacity_status": self.capacity_status,
            "observation_fencing_token": self.observation_fencing_token,
            "observation_version": self.observation_version,
            "observed_at": self.observed_at,
            "fresh_until": self.fresh_until,
        }


def _required(value: Mapping[str, Any], name: str) -> str:
    result = str(value.get(name, "")).strip()
    if not result or len(result) > 512:
        raise TurnPoolContractError(f"turn_pool_{name}_invalid")
    return result


def _positive(value: Mapping[str, Any], name: str) -> int:
    result = value.get(name)
    if isinstance(result, bool) or not isinstance(result, int) or result < 1:
        raise TurnPoolContractError(f"turn_pool_{name}_invalid")
    return result


def _timestamp(value: Mapping[str, Any], name: str) -> float:
    result = value.get(name)
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise TurnPoolContractError(f"turn_pool_{name}_invalid")
    return float(result)
