"""Temporal Activities that hand execution back to the Ananta hub."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any, Protocol

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ananta_contracts.temporal_workflow import (
    ArtifactReference,
    AuthorizationEnvelopeRef,
    ProbeRequest,
    StepActivityInput,
    StepActivityResult,
    TemporalContractError,
)
from worker.temporal.hub_gateway import (
    HubGatewayError,
    HubTaskGatewayPort,
    UnavailableHubTaskGateway,
)
from worker.temporal.retry_profiles import redacted_heartbeat_details


class AuthorizationVerifierPort(Protocol):
    def verify(self, envelope: AuthorizationEnvelopeRef, *, now: float) -> None: ...


class FailClosedAuthorizationVerifier:
    def verify(self, envelope: AuthorizationEnvelopeRef, *, now: float) -> None:
        raise TemporalContractError(
            "authorization_verifier_not_configured",
            "a cryptographic authorization verifier is required",
        )


@activity.defn(name="ananta.temporal.probe-activity.v1")
async def probe_activity(request: ProbeRequest) -> dict[str, str]:
    """Side-effect-free registration and connectivity probe."""

    return {
        "schema": request.schema,
        "request_id": request.request_id,
        "value": request.value,
        "status": "ok",
    }


class HubActivityGateway:
    """Activity adapter that delegates only to the hub-owned task system."""

    def __init__(
        self,
        *,
        gateway: HubTaskGatewayPort | None = None,
        authorization_verifier: AuthorizationVerifierPort | None = None,
        poll_seconds: float = 2.0,
        activity_timeout_seconds: float = 1_800.0,
    ) -> None:
        self._gateway = gateway or UnavailableHubTaskGateway()
        self._authorization_verifier = authorization_verifier or FailClosedAuthorizationVerifier()
        self._poll_seconds = max(0.05, min(float(poll_seconds), 60.0))
        self._activity_timeout_seconds = max(1.0, min(float(activity_timeout_seconds), 86_400.0))

    @activity.defn(name="ananta.hub-task.execute.v1")
    async def execute(self, raw_request: dict[str, Any]) -> dict[str, Any]:
        try:
            request = StepActivityInput.from_mapping(raw_request)
        except TemporalContractError as exc:
            raise ApplicationError(
                exc.reason_code,
                type="AnantaActivityContractError",
                non_retryable=True,
            ) from exc
        try:
            # Dataclass construction already checks plan/run/step binding.  The
            # injected verifier supplies signature, expiry, revocation and key
            # rotation checks without importing hub policy implementations.
            self._authorization_verifier.verify(request.authorization_envelope, now=time.time())
        except TemporalContractError as exc:
            raise ApplicationError(
                exc.reason_code,
                type="AnantaAuthorizationError",
                non_retryable=True,
            ) from exc

        attempt = int(activity.info().attempt)
        if attempt > 1:
            await self._consume_retry(request, attempt=attempt)

        receipt = await self._submit(request)
        if receipt.operation_id != request.operation_id:
            raise ApplicationError(
                "hub_operation_binding_mismatch",
                type="AnantaGatewayContractError",
                non_retryable=True,
            )

        started = time.monotonic()
        try:
            while receipt.status not in {"completed", "failed", "cancelled", "uncertain"}:
                if activity.is_cancelled():
                    await self._request_cancel(receipt.hub_task_id, request.operation_id)
                    return StepActivityResult(
                        operation_id=request.operation_id,
                        status="cancelled",
                        hub_task_id=receipt.hub_task_id,
                        attempt=activity.info().attempt,
                        reason_code="temporal_activity_cancelled",
                    ).to_dict()
                if time.monotonic() - started >= self._activity_timeout_seconds:
                    return StepActivityResult(
                        operation_id=request.operation_id,
                        status="uncertain",
                        hub_task_id=receipt.hub_task_id,
                        attempt=activity.info().attempt,
                        reason_code="hub_task_wait_timeout",
                    ).to_dict()
                activity.heartbeat(
                    redacted_heartbeat_details(
                        operation_id=request.operation_id,
                        hub_task_id=receipt.hub_task_id,
                        checkpoint_ref=receipt.checkpoint_ref,
                    )
                )
                await asyncio.sleep(self._poll_seconds)
                receipt = await self._get(receipt.hub_task_id, request.operation_id)
        except asyncio.CancelledError:
            await self._request_cancel(receipt.hub_task_id, request.operation_id)
            raise

        artifacts = tuple(ArtifactReference.from_mapping(item) for item in receipt.artifact_refs)
        return StepActivityResult(
            operation_id=request.operation_id,
            status=receipt.status,
            hub_task_id=receipt.hub_task_id,
            artifact_refs=artifacts,
            canonical_event_refs=receipt.canonical_event_refs,
            attempt=activity.info().attempt,
            reason_code=receipt.reason_code,
        ).to_dict()

    async def _submit(self, request: StepActivityInput):
        try:
            return await self._gateway.submit_authorized_task(request)
        except HubGatewayError as exc:
            raise ApplicationError(
                exc.reason_code,
                type="AnantaHubGatewayError",
                non_retryable=not exc.retryable,
            ) from exc

    async def _consume_retry(self, request: StepActivityInput, *, attempt: int) -> None:
        retry_binding = f"{request.tenant_id}:{request.run_id}:{request.operation_id}:{attempt}"
        retry_id = f"temporal_activity:{hashlib.sha256(retry_binding.encode('utf-8')).hexdigest()}"
        try:
            await self._gateway.consume_retry(
                request,
                retry_id=retry_id,
                category="temporal_activity",
            )
        except HubGatewayError as exc:
            raise ApplicationError(
                exc.reason_code,
                type="AnantaRetryBudgetError",
                non_retryable=not exc.retryable,
            ) from exc

    async def _get(self, hub_task_id: str, operation_id: str):
        try:
            return await self._gateway.get_task(hub_task_id=hub_task_id, operation_id=operation_id)
        except HubGatewayError as exc:
            raise ApplicationError(
                exc.reason_code,
                type="AnantaHubGatewayError",
                non_retryable=not exc.retryable,
            ) from exc

    async def _request_cancel(self, hub_task_id: str, operation_id: str) -> None:
        try:
            await self._gateway.request_cancel(
                hub_task_id=hub_task_id,
                operation_id=operation_id,
                reason="temporal_activity_cancelled",
            )
        except HubGatewayError:
            # The Temporal cancellation remains authoritative for the Activity.
            # Hub reconciliation observes the still-running task and can mark it
            # uncertain; swallowing here avoids masking cancellation itself.
            return


__all__ = [
    "AuthorizationVerifierPort",
    "FailClosedAuthorizationVerifier",
    "HubActivityGateway",
    "probe_activity",
]
