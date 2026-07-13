"""Observed workflow-runtime health for Hub selection and UI projection.

Capability declarations are static configuration; health is not.  This module
adapts the Hub-owned worker directory and explicitly configured runtime health
endpoints to the small ``RuntimeHealthPort`` used by runtime selection.  It
imports no worker or framework implementation and fails closed when no fresh
observation exists.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent.services.workflow_runtime_selection_service import RuntimeHealthSnapshot

RUNTIME_HEALTH_OBSERVATION_SCHEMA = "ananta.workflow_runtime_health_observation.v1"
_RUNTIME_ALIASES = {
    "ananta-native": frozenset({"ananta-native", "native"}),
    "langgraph": frozenset({"langgraph"}),
    "temporal": frozenset({"temporal"}),
}
_READY_AGENT_STATES = frozenset({"online", "busy"})


@dataclass(frozen=True)
class RuntimeHealthObservation:
    runtime_id: str
    instance_id: str
    status: str
    reason_code: str
    observed_at: float
    expires_at: float
    runtime_version: str = ""
    source: str = ""
    schema: str = RUNTIME_HEALTH_OBSERVATION_SCHEMA

    def assert_valid(self) -> None:
        if self.schema != RUNTIME_HEALTH_OBSERVATION_SCHEMA:
            raise ValueError("runtime_health_observation_schema_unsupported")
        if not self.runtime_id or not self.instance_id or not self.source:
            raise ValueError("runtime_health_observation_identity_invalid")
        if self.status not in {"ready", "degraded", "unavailable", "disabled"}:
            raise ValueError("runtime_health_observation_status_invalid")
        if not self.reason_code.startswith("runtime_health_"):
            raise ValueError("runtime_health_observation_reason_invalid")
        if self.observed_at <= 0 or self.expires_at <= self.observed_at:
            raise ValueError("runtime_health_observation_ttl_invalid")


class RuntimeHealthObservationSource(Protocol):
    def observations(self, runtime_id: str) -> tuple[RuntimeHealthObservation, ...]: ...


class AgentDirectoryRuntimeHealthSource:
    """Read runtime availability from fresh, validated Hub worker entries."""

    def __init__(
        self,
        *,
        load_agents: Callable[[], Sequence[Any]],
        stale_after_seconds: float = 120.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("runtime_health_stale_timeout_invalid")
        self._load_agents = load_agents
        self._stale_after_seconds = float(stale_after_seconds)
        self._clock = clock

    def observations(self, runtime_id: str) -> tuple[RuntimeHealthObservation, ...]:
        normalized = _normalize_runtime_id(runtime_id)
        aliases = _RUNTIME_ALIASES.get(normalized, frozenset({normalized}))
        capability_names = {f"workflow.adapter.{alias}" for alias in aliases}
        now = self._clock()
        observations: list[RuntimeHealthObservation] = []
        try:
            agents = self._load_agents()
        except Exception:
            return ()
        for agent in agents:
            if str(getattr(agent, "role", "worker") or "worker") != "worker":
                continue
            capabilities = {
                str(item).strip()
                for item in (getattr(agent, "capabilities", None) or ())
                if str(item).strip()
            }
            targets = _runtime_targets(getattr(agent, "runtime_targets", None) or ())
            if not (capability_names & capabilities or aliases & targets):
                continue
            last_seen = float(getattr(agent, "last_seen", 0.0) or 0.0)
            status = str(getattr(agent, "status", "offline") or "offline").lower()
            validated = bool(getattr(agent, "registration_validated", False))
            fresh = last_seen > 0 and now - last_seen <= self._stale_after_seconds
            if not validated:
                health_status = "unavailable"
                reason = "runtime_health_worker_registration_invalid"
            elif not fresh:
                health_status = "unavailable"
                reason = "runtime_health_worker_heartbeat_stale"
            elif status in _READY_AGENT_STATES:
                health_status = "ready"
                reason = "runtime_health_worker_ready"
            elif status == "degraded":
                health_status = "degraded"
                reason = "runtime_health_worker_degraded"
            else:
                health_status = "unavailable"
                reason = "runtime_health_worker_unavailable"
            observation = RuntimeHealthObservation(
                runtime_id=normalized,
                instance_id=str(
                    getattr(agent, "url", "")
                    or getattr(agent, "name", "")
                    or "unknown-worker"
                ),
                status=health_status,
                reason_code=reason,
                observed_at=now,
                expires_at=now + min(self._stale_after_seconds, 30.0),
                runtime_version=_target_version(
                    getattr(agent, "runtime_targets", None) or (), aliases
                ),
                source="hub_worker_directory",
            )
            observation.assert_valid()
            observations.append(observation)
        return tuple(observations)


class RuntimeHealthHttpClient(Protocol):
    def get_json(self, url: str, *, timeout_seconds: float) -> tuple[int, Mapping[str, Any]]: ...


class ConfiguredEndpointRuntimeHealthSource:
    """Probe dedicated runtime containers through explicitly configured URLs."""

    def __init__(
        self,
        *,
        endpoints: Mapping[str, str],
        client: RuntimeHealthHttpClient,
        timeout_seconds: float = 2.0,
        ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._endpoints = {
            _normalize_runtime_id(key): str(value).strip()
            for key, value in endpoints.items()
            if str(value).strip()
        }
        self._client = client
        self._timeout_seconds = max(0.1, min(float(timeout_seconds), 10.0))
        self._ttl_seconds = max(1.0, float(ttl_seconds))
        self._clock = clock

    def observations(self, runtime_id: str) -> tuple[RuntimeHealthObservation, ...]:
        normalized = _normalize_runtime_id(runtime_id)
        url = self._endpoints.get(normalized)
        if not url:
            return ()
        now = self._clock()
        try:
            status_code, payload = self._client.get_json(
                url, timeout_seconds=self._timeout_seconds
            )
            body = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
            reported_status = str(body.get("status") or "").strip().lower()
            ready = bool(body.get("ready", reported_status == "ready"))
            if status_code < 400 and ready:
                status = "ready"
                reason = "runtime_health_endpoint_ready"
            elif status_code < 500 and reported_status == "degraded":
                status = "degraded"
                reason = "runtime_health_endpoint_degraded"
            else:
                status = "unavailable"
                reason = "runtime_health_endpoint_unavailable"
            version = str(body.get("runtime_version") or "").strip()
        except Exception:
            status = "unavailable"
            reason = "runtime_health_endpoint_probe_failed"
            version = ""
        observation = RuntimeHealthObservation(
            runtime_id=normalized,
            instance_id=url,
            status=status,
            reason_code=reason,
            observed_at=now,
            expires_at=now + self._ttl_seconds,
            runtime_version=version,
            source="configured_health_endpoint",
        )
        observation.assert_valid()
        return (observation,)


class WorkflowRuntimeObservedHealthService:
    """Aggregate observations deterministically and expose RuntimeHealthPort."""

    def __init__(
        self,
        *,
        sources: Sequence[RuntimeHealthObservationSource],
        expected_versions: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sources = tuple(sources)
        self._expected_versions = {
            _normalize_runtime_id(key): str(value).strip()
            for key, value in (expected_versions or {}).items()
        }
        self._clock = clock

    def get_health(self, runtime_id: str) -> RuntimeHealthSnapshot:
        normalized = _normalize_runtime_id(runtime_id)
        now = self._clock()
        observations: list[RuntimeHealthObservation] = []
        for source in self._sources:
            try:
                candidates = source.observations(normalized)
            except Exception:
                continue
            for observation in candidates:
                try:
                    observation.assert_valid()
                except ValueError:
                    continue
                if observation.runtime_id == normalized and observation.expires_at > now:
                    observations.append(observation)
        if not observations:
            return RuntimeHealthSnapshot(
                normalized, "unavailable", "runtime_health_not_observed"
            )

        expected_version = self._expected_versions.get(normalized, "")
        version_mismatch = expected_version and any(
            item.runtime_version and item.runtime_version != expected_version
            for item in observations
        )
        if version_mismatch:
            return RuntimeHealthSnapshot(
                normalized, "degraded", "runtime_health_version_mismatch"
            )
        ranked = sorted(
            observations,
            key=lambda item: (
                {"ready": 0, "degraded": 1, "unavailable": 2, "disabled": 3}[
                    item.status
                ],
                item.instance_id,
            ),
        )
        selected = ranked[0]
        return RuntimeHealthSnapshot(
            normalized, selected.status, selected.reason_code
        )


class _DefaultRuntimeHealthHttpClient:
    def get_json(self, url: str, *, timeout_seconds: float) -> tuple[int, Mapping[str, Any]]:
        from agent.common.http import get_default_client

        response = get_default_client().get(
            url,
            timeout=timeout_seconds,
            return_response=True,
            silent=True,
        )
        if response is None:
            raise OSError("runtime health endpoint unavailable")
        payload = response.json()
        return int(response.status_code), payload if isinstance(payload, Mapping) else {}


def default_workflow_runtime_health_service() -> WorkflowRuntimeObservedHealthService:
    """Build the production Hub health adapter from current deployment state."""

    def load_agents() -> Sequence[Any]:
        from agent.services.repository_registry import get_repository_registry

        return get_repository_registry().agent_repo.get_all()

    stale_after = float(os.getenv("AGENT_OFFLINE_TIMEOUT") or 120.0)
    endpoints = {
        "ananta-native": os.getenv("ANANTA_NATIVE_WORKER_HEALTH_URL", ""),
        "langgraph": os.getenv("ANANTA_LANGGRAPH_WORKER_HEALTH_URL", ""),
        "temporal": os.getenv("ANANTA_TEMPORAL_WORKER_HEALTH_URL", ""),
    }
    return WorkflowRuntimeObservedHealthService(
        sources=(
            AgentDirectoryRuntimeHealthSource(
                load_agents=load_agents,
                stale_after_seconds=stale_after,
            ),
            ConfiguredEndpointRuntimeHealthSource(
                endpoints=endpoints,
                client=_DefaultRuntimeHealthHttpClient(),
            ),
        ),
        expected_versions={
            "ananta-native": "1.0.0",
            "langgraph": "1.0.0",
            "temporal": "1.0.0",
        },
    )


def _normalize_runtime_id(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return "ananta-native" if normalized == "native" else normalized


def _runtime_targets(values: Sequence[Any]) -> set[str]:
    targets: set[str] = set()
    for item in values:
        if isinstance(item, Mapping):
            for field in ("runtime_id", "adapter_id", "runtime_target_id"):
                value = str(item.get(field) or "").strip().lower()
                if value:
                    targets.add(value)
        else:
            value = str(item).strip().lower()
            if value:
                targets.add(value)
    return targets


def _target_version(values: Sequence[Any], aliases: frozenset[str]) -> str:
    for item in values:
        if not isinstance(item, Mapping):
            continue
        identity = {
            str(item.get(field) or "").strip().lower()
            for field in ("runtime_id", "adapter_id", "runtime_target_id")
        }
        if identity & aliases:
            return str(item.get("runtime_version") or item.get("version") or "").strip()
    return ""


__all__ = [
    "AgentDirectoryRuntimeHealthSource",
    "ConfiguredEndpointRuntimeHealthSource",
    "RuntimeHealthObservation",
    "WorkflowRuntimeObservedHealthService",
    "default_workflow_runtime_health_service",
]
