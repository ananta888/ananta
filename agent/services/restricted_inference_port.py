"""Control-plane port for dispatching restricted inference to a worker.

The port owns contract validation only.  Its injected transport may be backed
by the hub task queue, HTTP, or a test fake; this module never imports or
constructs a model adapter.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

from agent.services.restricted_inference_contract import (
    RestrictedInferenceContractError,
    RestrictedInferenceRequest,
    RestrictedInferenceResponse,
    validate_response_for_request,
)
from agent.services.restricted_inference_endpoint_policy import (
    AddressResolver,
    RestrictedInferenceEndpointResolutionError,
    pin_private_container_address,
    require_allowlisted_restricted_inference_endpoint,
)


@runtime_checkable
class RestrictedInferencePort(Protocol):
    def execute(self, request: RestrictedInferenceRequest) -> RestrictedInferenceResponse: ...


@runtime_checkable
class RestrictedInferenceTransport(Protocol):
    """Transport implemented by the hub-owned task delegation layer."""

    def dispatch(self, envelope: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ContractRestrictedInferencePort:
    """Validate both sides of an injected worker transport."""

    def __init__(self, transport: RestrictedInferenceTransport) -> None:
        self._transport = transport

    def execute(self, request: RestrictedInferenceRequest) -> RestrictedInferenceResponse:
        if not isinstance(request, RestrictedInferenceRequest):
            raise TypeError("request must be a RestrictedInferenceRequest")
        raw_response = self._transport.dispatch(request.to_dict())
        if not isinstance(raw_response, Mapping):
            raise RestrictedInferenceContractError(
                "invalid_worker_response",
                "restricted inference transport returned a non-object response",
            )
        response = RestrictedInferenceResponse.from_dict(raw_response)
        if response.request_id != request.request_id or response.task_id != request.task_id:
            raise RestrictedInferenceContractError(
                "response_correlation_mismatch",
                "worker response identifiers do not match the delegated request",
            )
        if response.operation is not request.operation:
            raise RestrictedInferenceContractError(
                "response_operation_mismatch",
                "worker response operation does not match the delegated request",
            )
        validate_response_for_request(request, response)
        return response


class HubTaskQueueRestrictedInferencePort:
    """Record every synchronous delegation as a Hub-owned child task.

    The dedicated runtime remains a capability worker, while queue ownership,
    correlation and terminal state stay in the Hub. Only identifiers, policy
    digests and operation names are persisted; input text is never copied into
    task metadata or audit details.
    """

    def __init__(self, delegate: RestrictedInferencePort) -> None:
        self._delegate = delegate

    def execute(self, request: RestrictedInferenceRequest) -> RestrictedInferenceResponse:
        from agent.common.audit import log_audit
        from agent.services.task_queue_service import get_task_queue_service
        from agent.services.voice_task_terminal_service import get_voice_task_terminal_service

        tracking_id = self._tracking_task_id(request)
        from agent.services.voice_task_scope import inherited_voice_task_scope

        inherited_scope = inherited_voice_task_scope(request.task_id, tenant_id=request.tenant_id)
        request_digest = hashlib.sha256(
            json.dumps(request.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        get_task_queue_service().ingest_task(
            task_id=tracking_id,
            status="in_progress",
            title=f"Restricted inference: {request.operation.value}",
            description="Hub-delegated bounded non-generative inference.",
            priority="medium",
            created_by="hub",
            source="restricted_inference",
            tags=["restricted_inference", "no_generation"],
            event_type="restricted_inference_delegated",
            event_channel="hub_task_queue",
            event_details={
                "request_id": request.request_id,
                "run_id": request.run_id,
                "operation": request.operation.value,
            },
            extra_fields={
                "task_kind": "restricted_inference",
                "parent_task_id": request.task_id,
                "required_capabilities": ["restricted_inference", request.operation.value],
                "worker_execution_context": {
                    "restricted_inference": {
                        "request_id": request.request_id,
                        "run_id": request.run_id,
                        "tenant_scope_hash": hashlib.sha256(request.tenant_id.encode()).hexdigest(),
                        **inherited_scope,
                        "operation": request.operation.value,
                        "manifest_id": request.model_manifest_id,
                        "policy_hash": request.policy_hash,
                        "request_digest": request_digest,
                        "no_generation": True,
                    }
                },
            },
        )
        try:
            response = self._delegate.execute(request)
        except Exception as exc:
            get_voice_task_terminal_service().update_existing(
                tracking_id,
                "failed",
                status_reason_code="restricted_inference_failed",
                status_reason_details={"error_type": type(exc).__name__},
                event_type="restricted_inference_failed",
                event_actor="hub",
                event_details={"request_id": request.request_id, "run_id": request.run_id},
            )
            log_audit(
                "restricted_inference_task_failed",
                {
                    "task_id": tracking_id,
                    "parent_task_id": request.task_id,
                    "request_id": request.request_id,
                    "run_id": request.run_id,
                    "operation": request.operation.value,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        outcome = "completed" if response.status.value == "succeeded" else "failed"
        get_voice_task_terminal_service().update_existing(
            tracking_id,
            outcome,
            event_type=f"restricted_inference_{outcome}",
            event_actor="hub",
            event_details={
                "request_id": request.request_id,
                "run_id": request.run_id,
                "status": response.status.value,
            },
            verification_status={
                "restricted_inference": {
                    "status": response.status.value,
                    "no_generation": response.no_generation,
                    "request_id": request.request_id,
                    "run_id": request.run_id,
                }
            },
        )
        log_audit(
            "restricted_inference_task_completed",
            {
                "task_id": tracking_id,
                "parent_task_id": request.task_id,
                "request_id": request.request_id,
                "run_id": request.run_id,
                "operation": request.operation.value,
                "status": response.status.value,
                "no_generation": response.no_generation,
            },
        )
        return response

    @staticmethod
    def _tracking_task_id(request: RestrictedInferenceRequest) -> str:
        correlation = f"{request.task_id}\0{request.request_id}\0{request.run_id}".encode()
        return f"restricted-inference-{hashlib.sha256(correlation).hexdigest()[:32]}"


class RestrictedInferenceTransportError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise RestrictedInferenceTransportError("worker_redirect_forbidden", "worker transport refused a redirect")


class HttpRestrictedInferenceTransport:
    """Bounded authenticated transport to the internal worker endpoint."""

    def __init__(
        self,
        *,
        endpoint: str,
        allowed_endpoints: tuple[str, ...],
        bearer_token: str,
        connect_timeout_seconds: float = 5.0,
        max_response_bytes: int = 4 * 1024 * 1024,
        resolver: AddressResolver | None = None,
        opener: Any | None = None,
    ) -> None:
        normalized = require_allowlisted_restricted_inference_endpoint(
            endpoint,
            allowed_endpoints,
        )
        parsed = urlsplit(normalized)
        token = str(bearer_token or "").strip()
        if len(token) < 24:
            raise ValueError("bearer_token must contain at least 24 characters")
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive")
        if not 1024 <= max_response_bytes <= 64 * 1024 * 1024:
            raise ValueError("max_response_bytes must be between 1 KiB and 64 MiB")
        self._parsed = parsed
        self._token = token
        self._connect_timeout = connect_timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._resolver = resolver
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    def dispatch(self, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        contract_request = RestrictedInferenceRequest.from_dict(envelope)
        remaining_seconds = (contract_request.deadline_epoch_ms - time.time_ns() // 1_000_000) / 1000.0
        if remaining_seconds <= 0:
            raise RestrictedInferenceTransportError("timeout", "worker deadline expired before dispatch")
        address = self._private_pinned_address()
        netloc = f"[{address}]:{self._parsed.port}" if ":" in address else f"{address}:{self._parsed.port}"
        pinned_endpoint = urlunsplit(("http", netloc, self._parsed.path, "", ""))
        body = json.dumps(dict(envelope), separators=(",", ":"), allow_nan=False).encode("utf-8")
        http_request = urllib.request.Request(
            pinned_endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Host": self._parsed.netloc,
            },
        )
        timeout = min(self._connect_timeout, remaining_seconds)
        try:
            response = self._opener.open(http_request, timeout=timeout)
            payload = self._read_json_response(response)
        except urllib.error.HTTPError as exc:
            payload = self._read_json_response(exc)
        except RestrictedInferenceTransportError:
            raise
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise RestrictedInferenceTransportError(
                "worker_unavailable",
                "restricted inference worker is unavailable",
            ) from exc
        if not isinstance(payload, Mapping):
            raise RestrictedInferenceTransportError("invalid_worker_response", "worker response must be an object")
        return payload

    def _private_pinned_address(self) -> str:
        hostname = str(self._parsed.hostname or "")
        port = int(self._parsed.port or 0)
        try:
            return pin_private_container_address(
                hostname,
                port,
                resolver=self._resolver,
            )
        except RestrictedInferenceEndpointResolutionError as exc:
            raise RestrictedInferenceTransportError(exc.reason_code, str(exc)) from exc

    def _read_json_response(self, response: Any) -> Any:
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "application/json" not in content_type:
            raise RestrictedInferenceTransportError("invalid_worker_response", "worker response must be JSON")
        data = response.read(self._max_response_bytes + 1)
        if len(data) > self._max_response_bytes:
            raise RestrictedInferenceTransportError("worker_response_too_large", "worker response exceeds size limit")
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RestrictedInferenceTransportError("invalid_worker_response", "worker returned invalid JSON") from exc
