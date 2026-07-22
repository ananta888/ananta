"""Hub-owned, compensating broadcast admission integration."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from agent.repositories.sfu_broadcast_admission_operation_repository import (
    SfuBroadcastAdmissionOperationCommand,
    SfuBroadcastAdmissionOperationRecord,
    SfuBroadcastAdmissionOperationRepositoryPort,
)


class SfuBroadcastAdmissionError(RuntimeError):
    def __init__(self, reason_code: str, status_code: int = 503) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuBroadcastAdmissionPlan:
    tenant_id: str
    room_id: str
    actor_id: str
    operation: str
    idempotency_key: str
    expected_version: int
    deadline_at: float
    flag_version: int
    cohort_version: int
    cluster_id: str
    region: str
    runtime_control_mode: str
    runtime_instance_id: str | None
    directory_version: int
    fencing_token: int
    membership_epoch: int
    route_epoch: int
    topology_epoch: int
    request: Mapping[str, object]

    def bindings(self) -> dict[str, object]:
        return {
            "flag_version": self.flag_version,
            "cohort_version": self.cohort_version,
            "cluster_id": self.cluster_id,
            "region": self.region,
            "runtime_control_mode": self.runtime_control_mode,
            "runtime_instance_id": self.runtime_instance_id,
            "directory_version": self.directory_version,
            "fencing_token": self.fencing_token,
            "membership_epoch": self.membership_epoch,
            "route_epoch": self.route_epoch,
            "topology_epoch": self.topology_epoch,
        }


@dataclass(frozen=True, slots=True)
class SfuBroadcastPreparedResource:
    resource_id: str
    external_request_id: str
    bindings: Mapping[str, object]


class SfuBroadcastAdmissionReadinessPort(Protocol):
    def require_ready(self, plan: SfuBroadcastAdmissionPlan) -> None: ...


class SfuBroadcastAdmissionResourcePort(Protocol):
    def prepare(self, operation_id: str, plan: SfuBroadcastAdmissionPlan) -> SfuBroadcastPreparedResource: ...

    def compensate(self, operation_id: str, resource_id: str) -> None: ...


class SfuBroadcastAdmissionPlanResolverPort(Protocol):
    def resolve(
        self,
        operation: str,
        request: Mapping[str, Any],
        *,
        actor_id: str,
        tenant_id: str,
    ) -> SfuBroadcastAdmissionPlan: ...


class UnavailableSfuBroadcastAdmissionPort:
    def require_ready(self, plan: SfuBroadcastAdmissionPlan) -> None:
        del plan
        raise SfuBroadcastAdmissionError("sfu_broadcast_admission_readiness_unknown")

    def prepare(self, operation_id: str, plan: SfuBroadcastAdmissionPlan) -> SfuBroadcastPreparedResource:
        del operation_id, plan
        raise SfuBroadcastAdmissionError("sfu_broadcast_admission_port_unavailable")

    def compensate(self, operation_id: str, resource_id: str) -> None:
        del operation_id, resource_id


class UnavailableSfuBroadcastAdmissionPlanResolver:
    def resolve(
        self,
        operation: str,
        request: Mapping[str, Any],
        *,
        actor_id: str,
        tenant_id: str,
    ) -> SfuBroadcastAdmissionPlan:
        del operation, request, actor_id, tenant_id
        raise SfuBroadcastAdmissionError("sfu_broadcast_admission_plan_unavailable")


class SfuBroadcastAdmissionSaga:
    """Coordinates independent repositories without pretending they share a transaction."""

    _STEPS = ("capacity", "identity", "route")

    def __init__(
        self,
        repository: SfuBroadcastAdmissionOperationRepositoryPort,
        readiness: SfuBroadcastAdmissionReadinessPort,
        capacity: SfuBroadcastAdmissionResourcePort,
        identity: SfuBroadcastAdmissionResourcePort,
        route: SfuBroadcastAdmissionResourcePort,
        *,
        clock=time.time,
    ) -> None:
        self._repository = repository
        self._readiness = readiness
        self._ports = {"capacity": capacity, "identity": identity, "route": route}
        self._clock = clock

    def prepare(self, plan: SfuBroadcastAdmissionPlan) -> SfuBroadcastAdmissionOperationRecord:
        now = float(self._clock())
        operation = self._repository.begin(
            SfuBroadcastAdmissionOperationCommand(
                tenant_id=plan.tenant_id,
                room_id=plan.room_id,
                actor_id=plan.actor_id,
                operation=plan.operation,
                idempotency_key=plan.idempotency_key,
                request=plan.request,
                expected_version=plan.expected_version,
                deadline_at=plan.deadline_at,
            ),
            now=now,
        )
        if operation.status == "completed":
            return operation
        if operation.deadline_at <= now:
            self.compensate(operation, "sfu_broadcast_admission_deadline_expired")
            raise SfuBroadcastAdmissionError("sfu_broadcast_admission_deadline_expired")
        self._readiness.require_ready(plan)
        operation = self._advance_binding(operation, "placement", None, plan.bindings())
        try:
            for step in self._STEPS:
                if step in operation.applied_steps:
                    continue
                prepared = self._ports[step].prepare(operation.id, plan)
                operation = self._advance_binding(
                    operation,
                    step,
                    prepared.external_request_id,
                    {f"{step}_resource_id": prepared.resource_id, **dict(prepared.bindings)},
                )
        except Exception as exc:
            reason = getattr(exc, "reason_code", "sfu_broadcast_admission_prepare_failed")
            self.compensate(operation, str(reason))
            if isinstance(exc, SfuBroadcastAdmissionError):
                raise
            raise SfuBroadcastAdmissionError(str(reason)) from exc
        return operation

    def complete(
        self,
        operation: SfuBroadcastAdmissionOperationRecord,
        result: Mapping[str, object],
    ) -> SfuBroadcastAdmissionOperationRecord:
        digestable = {key: value for key, value in result.items() if key != "access_token"}
        result_digest = hashlib.sha256(
            json.dumps(digestable, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        return self._repository.finish(
            operation.id,
            expected_version=operation.version,
            status="completed",
            reason_code="accepted",
            result_digest=result_digest,
            compensation={},
            now=float(self._clock()),
        )

    def compensate(
        self,
        operation: SfuBroadcastAdmissionOperationRecord,
        reason_code: str,
    ) -> SfuBroadcastAdmissionOperationRecord:
        outcomes: dict[str, str] = {}
        for step in reversed(self._STEPS):
            resource_id = operation.bindings.get(f"{step}_resource_id")
            if step not in operation.applied_steps or not isinstance(resource_id, str):
                continue
            try:
                self._ports[step].compensate(operation.id, resource_id)
            except Exception:
                outcomes[step] = "compensation_pending"
            else:
                outcomes[step] = "compensated"
        status = "compensated" if all(value == "compensated" for value in outcomes.values()) else "failed"
        return self._repository.finish(
            operation.id,
            expected_version=operation.version,
            status=status,
            reason_code=reason_code,
            result_digest=None,
            compensation=outcomes,
            now=float(self._clock()),
        )

    def recover_open(self, *, limit: int) -> dict[str, int]:
        operations = self._repository.open(limit=limit, now=float(self._clock()))
        compensated = failed = 0
        for operation in operations:
            try:
                result = self.compensate(operation, "sfu_broadcast_admission_recovered")
            except Exception:
                failed += 1
            else:
                compensated += result.status == "compensated"
                failed += result.status != "compensated"
        return {"processed": len(operations), "compensated": compensated, "failed": failed}

    def _advance_binding(
        self,
        operation: SfuBroadcastAdmissionOperationRecord,
        step: str,
        request_id: str | None,
        bindings: Mapping[str, object],
    ) -> SfuBroadcastAdmissionOperationRecord:
        return self._repository.advance(
            operation.id,
            expected_version=operation.version,
            step=step,
            external_request_id=request_id,
            bindings=bindings,
            now=float(self._clock()),
        )


class SfuBroadcastAdmissionFacade:
    """Preserves legacy admission while routing explicit broadcast topology through the saga."""

    def __init__(
        self,
        delegate: object,
        saga: SfuBroadcastAdmissionSaga,
        resolver: SfuBroadcastAdmissionPlanResolverPort,
    ) -> None:
        self._delegate = delegate
        self._saga = saga
        self._resolver = resolver

    def join(self, request: Mapping[str, Any], *, actor_id: str, tenant_id: str) -> dict[str, Any]:
        return self._execute("join", "join", request, actor_id=actor_id, tenant_id=tenant_id)

    def authorize_publication(self, request: Mapping[str, Any], *, actor_id: str, tenant_id: str) -> dict[str, Any]:
        return self._execute("publish", "authorize_publication", request, actor_id=actor_id, tenant_id=tenant_id)

    def authorize_subscription(self, request: Mapping[str, Any], *, actor_id: str, tenant_id: str) -> dict[str, Any]:
        return self._execute("subscribe", "authorize_subscription", request, actor_id=actor_id, tenant_id=tenant_id)

    def _execute(
        self,
        operation: str,
        method_name: str,
        request: Mapping[str, Any],
        *,
        actor_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        if request.get("media_topology") != "sfu_broadcast":
            return getattr(self._delegate, method_name)(request, actor_id=actor_id, tenant_id=tenant_id)
        plan = self._resolver.resolve(operation, request, actor_id=actor_id, tenant_id=tenant_id)
        prepared = self._saga.prepare(plan)
        try:
            result = getattr(self._delegate, method_name)(request, actor_id=actor_id, tenant_id=tenant_id)
            self._saga.complete(prepared, result)
            return result
        except Exception:
            self._saga.compensate(prepared, "sfu_broadcast_token_or_authorization_failed")
            raise

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)


__all__ = [
    "SfuBroadcastAdmissionError",
    "SfuBroadcastAdmissionFacade",
    "SfuBroadcastAdmissionPlan",
    "SfuBroadcastAdmissionPlanResolverPort",
    "SfuBroadcastAdmissionReadinessPort",
    "SfuBroadcastAdmissionResourcePort",
    "SfuBroadcastAdmissionSaga",
    "SfuBroadcastPreparedResource",
    "UnavailableSfuBroadcastAdmissionPlanResolver",
    "UnavailableSfuBroadcastAdmissionPort",
]
