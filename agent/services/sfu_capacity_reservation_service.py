"""Hub admission policy for atomic SFU capacity reservations."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping

from agent.repositories.sfu_capacity_reservation_repository import (
    SfuCapacityMutation,
    SfuCapacityMutationResult,
    SfuCapacityReservationError,
    SfuCapacityReservationRepositoryPort,
    SfuResourceVector,
)
from agent.services.sfu_broadcast_route_port import RuntimeControlModeV1


@dataclass(frozen=True, slots=True)
class SfuCapacityLimitSet:
    cluster: SfuResourceVector
    tenant: SfuResourceVector


@dataclass(frozen=True, slots=True)
class SfuCapacityReservationPolicy:
    enabled: bool
    profile_limits: Mapping[str, SfuCapacityLimitSet]
    lease_ttl_seconds_max: float = 300.0

    @classmethod
    def fail_closed(cls) -> "SfuCapacityReservationPolicy":
        return cls(enabled=False, profile_limits={})


@dataclass(frozen=True, slots=True)
class SfuCapacityReservationRequest:
    command_id: str
    operation: str
    tenant_id: str
    room_id: str
    cluster_id: str
    region: str
    runtime_control_mode: str
    placement_owner: str
    observed_node_id: str | None
    runtime_instance_id: str | None
    infrastructure_profile_id: str
    slo_profile_id: str
    resources: SfuResourceVector
    lease_ttl_seconds: float
    directory_version: int
    expected_version: int
    observation_expires_at: float
    target_admission_ready: bool
    compatible: bool


class SfuCapacityReservationService:
    """Validates placement invariants before entering the SQL ledger."""

    def __init__(
        self,
        repository: SfuCapacityReservationRepositoryPort,
        policy: SfuCapacityReservationPolicy,
        *,
        clock=time.time,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._clock = clock

    def change(self, request: SfuCapacityReservationRequest) -> SfuCapacityMutationResult:
        now = float(self._clock())
        self._validate(request)
        if not self._policy.enabled:
            raise SfuCapacityReservationError("sfu_capacity_activation_disabled")
        limits = self._policy.profile_limits.get(request.slo_profile_id)
        if limits is None:
            raise SfuCapacityReservationError("sfu_capacity_profile_unknown")
        if not request.compatible:
            raise SfuCapacityReservationError("sfu_capacity_target_incompatible")
        mutation = SfuCapacityMutation(
            command_id=request.command_id,
            operation=request.operation,
            tenant_id=request.tenant_id,
            room_id=request.room_id,
            cluster_id=request.cluster_id,
            region=request.region,
            runtime_control_mode=request.runtime_control_mode,
            placement_owner=request.placement_owner,
            observed_node_id=request.observed_node_id,
            runtime_instance_id=request.runtime_instance_id,
            infrastructure_profile_id=request.infrastructure_profile_id,
            slo_profile_id=request.slo_profile_id,
            resources=request.resources,
            lease_ttl_seconds=request.lease_ttl_seconds,
            directory_version=request.directory_version,
            expected_version=request.expected_version,
            observation_fresh=now < request.observation_expires_at,
            target_admission_ready=request.target_admission_ready,
        )
        return self._repository.mutate(
            mutation,
            cluster_limit=limits.cluster,
            tenant_limit=limits.tenant,
            now=now,
        )

    def _validate(self, request: SfuCapacityReservationRequest) -> None:
        for value, reason in (
            (request.command_id, "sfu_capacity_command_id_required"),
            (request.tenant_id, "sfu_capacity_tenant_required"),
            (request.room_id, "sfu_capacity_room_required"),
            (request.cluster_id, "sfu_capacity_cluster_required"),
            (request.region, "sfu_capacity_region_required"),
            (request.infrastructure_profile_id, "sfu_capacity_infrastructure_profile_required"),
            (request.slo_profile_id, "sfu_capacity_slo_profile_required"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise SfuCapacityReservationError(reason)
        if request.operation not in {"create", "renew", "resize", "release"}:
            raise SfuCapacityReservationError("sfu_capacity_operation_invalid")
        if (
            isinstance(request.lease_ttl_seconds, bool)
            or not isinstance(request.lease_ttl_seconds, (int, float))
            or not 1 <= request.lease_ttl_seconds <= self._policy.lease_ttl_seconds_max
        ):
            raise SfuCapacityReservationError("sfu_capacity_lease_ttl_invalid")
        if request.directory_version < 1 or request.expected_version < 0:
            raise SfuCapacityReservationError("sfu_capacity_version_invalid")

        if request.runtime_control_mode == RuntimeControlModeV1.LIVEKIT_CONTROL_API.value:
            if request.placement_owner != "livekit_native":
                raise SfuCapacityReservationError("sfu_capacity_placement_owner_mismatch")
            if request.runtime_instance_id is not None:
                raise SfuCapacityReservationError("sfu_capacity_native_node_selection_forbidden")
        elif (
            request.runtime_control_mode
            == RuntimeControlModeV1.AUTHENTICATED_RUNTIME_EXTENSION.value
        ):
            if request.placement_owner not in {
                "authenticated_runtime_extension",
                "hub_cluster_only",
            }:
                raise SfuCapacityReservationError("sfu_capacity_placement_owner_mismatch")
            if not request.runtime_instance_id:
                raise SfuCapacityReservationError("sfu_capacity_runtime_instance_required")
        else:
            raise SfuCapacityReservationError("sfu_capacity_runtime_mode_unknown")


__all__ = [
    "SfuCapacityLimitSet",
    "SfuCapacityReservationPolicy",
    "SfuCapacityReservationRequest",
    "SfuCapacityReservationService",
]
