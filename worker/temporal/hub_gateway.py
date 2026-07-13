"""Hub-task gateway port used by Temporal Activities.

The Temporal worker never selects or calls an Ananta worker.  It submits one
idempotent, authorization-bound command to the hub and polls the hub-owned task
read model.  The HTTP implementation intentionally targets a dedicated
internal command endpoint; falling back to public task creation would bypass
authorization-envelope and side-effect-ledger revalidation and is therefore
not allowed.
"""

from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol

from ananta_contracts.hub_task_gateway import (
    HUB_TASK_COMMAND_SCHEMA,
    HUB_TASK_RECEIPT_SCHEMA,
    HubTaskContractError,
    HubTaskReceipt,
    RetryBudgetReceipt,
)
from ananta_contracts.temporal_workflow import StepActivityInput


class HubGatewayError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code or "hub_gateway_failed")
        self.retryable = bool(retryable)


class HubTaskGatewayPort(Protocol):
    async def consume_retry(
        self,
        request: StepActivityInput,
        *,
        retry_id: str,
        category: str,
    ) -> RetryBudgetReceipt: ...

    async def submit_authorized_task(self, request: StepActivityInput) -> HubTaskReceipt: ...

    async def get_task(self, *, hub_task_id: str, operation_id: str) -> HubTaskReceipt: ...

    async def request_cancel(self, *, hub_task_id: str, operation_id: str, reason: str) -> None: ...


class UnavailableHubTaskGateway:
    async def consume_retry(
        self,
        request: StepActivityInput,
        *,
        retry_id: str,
        category: str,
    ) -> RetryBudgetReceipt:
        del request, retry_id, category
        raise HubGatewayError("hub_activity_gateway_not_configured", retryable=False)

    async def submit_authorized_task(self, request: StepActivityInput) -> HubTaskReceipt:
        raise HubGatewayError("hub_activity_gateway_not_configured", retryable=False)

    async def get_task(self, *, hub_task_id: str, operation_id: str) -> HubTaskReceipt:
        raise HubGatewayError("hub_activity_gateway_not_configured", retryable=False)

    async def request_cancel(self, *, hub_task_id: str, operation_id: str, reason: str) -> None:
        raise HubGatewayError("hub_activity_gateway_not_configured", retryable=False)


class HttpHubTaskGateway:
    """Strict internal HTTP adapter; no direct worker routing exists here."""

    def __init__(
        self,
        *,
        hub_url: str,
        bearer_token: str,
        command_path: str = "/api/internal/workflow-runtime/tasks",
        timeout_seconds: float = 15.0,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._hub_url = str(hub_url or "").rstrip("/")
        self._bearer_token = str(bearer_token or "")
        self._command_path = "/" + str(command_path or "").strip("/")
        self._retry_path = f"{self._command_path.rsplit('/', 1)[0]}/retries"
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        self._ssl_context = ssl_context
        if not self._hub_url.startswith(("http://", "https://")):
            raise ValueError("hub_url must use HTTP or HTTPS")
        if not self._bearer_token or len(self._bearer_token) > 16_384:
            raise ValueError("hub bearer token is invalid")

    async def submit_authorized_task(self, request: StepActivityInput) -> HubTaskReceipt:
        payload = _activity_command(request, command="submit")
        return _receipt_from_mapping(await self._request_json("POST", self._command_path, payload))

    async def consume_retry(
        self,
        request: StepActivityInput,
        *,
        retry_id: str,
        category: str,
    ) -> RetryBudgetReceipt:
        payload = {
            **_activity_command(request, command="consume_retry"),
            "retry_id": str(retry_id),
            "retry_category": str(category),
        }
        raw = await self._request_json("POST", self._retry_path, payload)
        try:
            return RetryBudgetReceipt.from_mapping(raw)
        except HubTaskContractError as exc:
            raise HubGatewayError(exc.reason_code, retryable=False) from exc

    async def get_task(self, *, hub_task_id: str, operation_id: str) -> HubTaskReceipt:
        safe_task_id = urllib.parse.quote(str(hub_task_id), safe="")
        payload = {
            "schema": HUB_TASK_COMMAND_SCHEMA,
            "command": "status",
            "operation_id": str(operation_id),
        }
        path = f"{self._command_path}/{safe_task_id}/commands"
        return _receipt_from_mapping(await self._request_json("POST", path, payload))

    async def request_cancel(self, *, hub_task_id: str, operation_id: str, reason: str) -> None:
        safe_task_id = urllib.parse.quote(str(hub_task_id), safe="")
        payload = {
            "schema": HUB_TASK_COMMAND_SCHEMA,
            "command": "cancel",
            "operation_id": operation_id,
            "reason": str(reason or "temporal_activity_cancelled")[:256],
        }
        await self._request_json("POST", f"{self._command_path}/{safe_task_id}/commands", payload)

    async def _request_json(self, method: str, path: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
        return await asyncio.to_thread(self._request_json_sync, method, path, payload)

    def _request_json_sync(self, method: str, path: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._bearer_token}",
            "User-Agent": "ananta-temporal-worker/1",
        }
        if payload is not None:
            body = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self._hub_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._timeout_seconds,
                context=self._ssl_context,
            ) as response:
                raw = response.read(1_048_577)
        except urllib.error.HTTPError as exc:
            retryable = int(exc.code) >= 500 or int(exc.code) in {408, 425, 429}
            raise HubGatewayError(_http_error_reason(exc), retryable=retryable) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HubGatewayError("hub_unavailable", retryable=True) from exc
        if len(raw) > 1_048_576:
            raise HubGatewayError("hub_response_too_large", retryable=False)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HubGatewayError("invalid_hub_json", retryable=False) from exc
        if not isinstance(decoded, Mapping):
            raise HubGatewayError("invalid_hub_response", retryable=False)
        data = decoded.get("data") if isinstance(decoded.get("data"), Mapping) else decoded
        return dict(data)


def _receipt_from_mapping(raw: object) -> HubTaskReceipt:
    try:
        return HubTaskReceipt.from_mapping(raw)
    except HubTaskContractError as exc:
        raise HubGatewayError(exc.reason_code, retryable=False) from exc


def _activity_command(request: StepActivityInput, *, command: str) -> dict[str, Any]:
    return {
        "schema": HUB_TASK_COMMAND_SCHEMA,
        "command": command,
        "operation_id": request.operation_id,
        "tenant_id": request.tenant_id,
        "workflow_id": request.workflow_id,
        "run_id": request.run_id,
        "correlation_id": request.correlation_id,
        "step_id": request.step_id,
        "plan_hash": request.plan_hash,
        "task_kind": request.task_kind,
        "authorization_envelope": request.authorization_envelope.to_dict(),
        "artifact_refs": [item.to_dict() for item in request.artifact_refs],
        "required_capabilities": list(request.required_capabilities),
        "activity_class": request.activity_class.value,
        "retry_budget_remaining": request.retry_budget_remaining,
        "retry_budget_maximum": request.retry_budget_maximum,
        "parameters": dict(request.parameters),
    }


def _http_error_reason(error: urllib.error.HTTPError) -> str:
    """Retain only a bounded machine reason from authenticated Hub errors."""

    try:
        raw = error.read(16_385)
        if len(raw) > 16_384:
            return f"hub_http_{error.code}"
        decoded = json.loads(raw.decode("utf-8"))
        data = decoded.get("data") if isinstance(decoded, Mapping) else None
        reason = str(data.get("reason_code") or "") if isinstance(data, Mapping) else ""
    except (OSError, UnicodeError, json.JSONDecodeError):
        return f"hub_http_{error.code}"
    invalid_character = any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789_:-" for character in reason
    )
    if not reason or len(reason) > 256 or invalid_character:
        return f"hub_http_{error.code}"
    return reason


__all__ = [
    "HUB_TASK_COMMAND_SCHEMA",
    "HUB_TASK_RECEIPT_SCHEMA",
    "HttpHubTaskGateway",
    "HubGatewayError",
    "HubTaskContractError",
    "HubTaskGatewayPort",
    "HubTaskReceipt",
    "RetryBudgetReceipt",
    "UnavailableHubTaskGateway",
]
