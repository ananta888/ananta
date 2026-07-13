"""Bearer-authenticated worker adapters for Hub-owned workflow decisions."""

from __future__ import annotations

import json
import os
import ssl
import stat
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ananta_contracts.hub_task_gateway import HubTaskContractError, RetryBudgetReceipt
from ananta_contracts.provider_invocation import (
    ProviderBudgetDecision,
    ProviderInvocationBlocked,
    ProviderInvocationContext,
)
from ananta_contracts.workflow_worker_gateway import (
    WORKFLOW_WORKER_COMMAND_SCHEMA,
    WORKFLOW_WORKER_DECISION_SCHEMA,
    ProviderBudgetReceipt,
    SideEffectGatewayReceipt,
    WorkflowWorkerContractError,
)
from worker.core.tool_calling_pipeline import ToolCallDecision, ToolCallRequest
from worker.core.tool_registry import WorkerToolEntry
from worker.runtime.workflow_adapter_task_consumer import ExecutionAuthorizationDecision


class WorkflowHubDecisionError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        self.reason_code = str(reason_code or "workflow_hub_decision_failed")
        self.retryable = bool(retryable)
        super().__init__(self.reason_code)


class HttpWorkflowHubDecisionClient:
    """POST-only transport; signed bindings stay in the request body."""

    def __init__(
        self,
        *,
        hub_url: str,
        bearer_token: str,
        command_path: str = "/api/internal/workflow-runtime/worker-commands",
        timeout_seconds: float = 15.0,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(str(hub_url or "").rstrip("/"))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("workflow Hub URL is invalid")
        self._hub_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
        )
        self._bearer_token = str(bearer_token or "")
        token_bytes = self._bearer_token.encode("utf-8")
        if (
            not 32 <= len(token_bytes) <= 16_384
            or "\x00" in self._bearer_token
            or any(character.isspace() for character in self._bearer_token)
        ):
            raise ValueError("workflow Hub bearer token is invalid")
        self._command_path = "/" + str(command_path or "").strip("/")
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        self._ssl_context = ssl_context

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> "HttpWorkflowHubDecisionClient | None":
        source = os.environ if env is None else env
        hub_url = str(
            source.get("ANANTA_WORKFLOW_HUB_URL")
            or source.get("ANANTA_LANGGRAPH_HUB_URL")
            or ""
        ).strip()
        token_file = str(
            source.get("ANANTA_WORKFLOW_HUB_TOKEN_FILE")
            or source.get("ANANTA_LANGGRAPH_HUB_TOKEN_FILE")
            or ""
        ).strip()
        if not hub_url and not token_file:
            return None
        if not hub_url or not token_file:
            raise ValueError("workflow Hub URL and token file are both required")
        return cls(
            hub_url=hub_url,
            bearer_token=_read_token_file(token_file),
        )

    def command(self, command: str, *, binding: Mapping[str, Any], **values: Any) -> dict[str, Any]:
        payload = {
            "schema": WORKFLOW_WORKER_COMMAND_SCHEMA,
            "command": str(command),
            "binding": dict(binding),
            **values,
        }
        body = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if len(body) > 262_144:
            raise WorkflowHubDecisionError("workflow_worker_command_too_large")
        request = urllib.request.Request(
            self._hub_url + self._command_path,
            data=body,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._bearer_token}",
                "Content-Type": "application/json",
                "User-Agent": "ananta-workflow-worker/1",
            },
            method="POST",
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
            raise WorkflowHubDecisionError(
                _http_error_reason(exc), retryable=retryable
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise WorkflowHubDecisionError("workflow_hub_unavailable", retryable=True) from exc
        if len(raw) > 1_048_576:
            raise WorkflowHubDecisionError("workflow_hub_response_too_large")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise WorkflowHubDecisionError("workflow_hub_response_invalid") from exc
        if not isinstance(decoded, Mapping):
            raise WorkflowHubDecisionError("workflow_hub_response_invalid")
        nested = decoded.get("data")
        data: Mapping[Any, Any] = nested if isinstance(nested, Mapping) else decoded
        return {str(key): value for key, value in data.items()}


class HubToolAuthorizationAdapter:
    def __init__(self, client: HttpWorkflowHubDecisionClient) -> None:
        self._client = client

    def verify(self, request: ToolCallRequest, descriptor: WorkerToolEntry) -> ToolCallDecision:
        try:
            response = self._client.command(
                "authorize_tool",
                binding=_tool_binding(request),
                **_tool_command_values(request, descriptor),
            )
        except WorkflowHubDecisionError as exc:
            return ToolCallDecision(False, exc.reason_code)
        if response.get("schema") != WORKFLOW_WORKER_DECISION_SCHEMA:
            return ToolCallDecision(False, "workflow_hub_decision_invalid")
        return ToolCallDecision(
            bool(response.get("allowed", False)),
            str(response.get("reason_code") or "workflow_hub_decision_denied"),
            dict(response),
        )


class HubExecutionAuthorizationAdapter:
    """Revalidate one already-delegated adapter execution and its active lease."""

    def __init__(self, client: HttpWorkflowHubDecisionClient) -> None:
        self._client = client

    def authorize(
        self,
        *,
        binding,
        adapter_kind: str,
        attempt_id: str,
        fencing_token: int,
    ) -> ExecutionAuthorizationDecision:
        try:
            response = self._client.command(
                "authorize_execution",
                binding=binding.to_dict(),
                adapter_kind=str(adapter_kind),
                attempt_id=str(attempt_id),
                fencing_token=int(fencing_token),
            )
        except (TypeError, ValueError, WorkflowHubDecisionError) as exc:
            return ExecutionAuthorizationDecision(
                False,
                getattr(exc, "reason_code", "workflow_hub_authorization_unavailable"),
            )
        if response.get("schema") != WORKFLOW_WORKER_DECISION_SCHEMA:
            return ExecutionAuthorizationDecision(False, "workflow_hub_decision_invalid")
        return ExecutionAuthorizationDecision(
            bool(response.get("allowed", False)),
            str(response.get("reason_code") or "workflow_hub_decision_denied"),
        )


class HubSideEffectLedgerAdapter:
    def __init__(self, client: HttpWorkflowHubDecisionClient) -> None:
        self._client = client

    def claim(
        self,
        *,
        operation_id: str,
        fencing_token: int,
        metadata: dict[str, Any],
    ) -> ToolCallDecision:
        try:
            response = self._client.command(
                "side_effect_claim",
                binding=_metadata_binding(metadata),
                operation_id=operation_id,
                fencing_token=int(fencing_token),
                attempt_id=str(metadata.get("attempt_id") or ""),
                tool_id=str(metadata.get("tool_id") or ""),
                side_effect_class=str(metadata.get("side_effect_class") or ""),
                **_tool_approval_values(metadata),
            )
            receipt = SideEffectGatewayReceipt.from_mapping(response)
        except (WorkflowHubDecisionError, WorkflowWorkerContractError) as exc:
            return ToolCallDecision(False, getattr(exc, "reason_code", str(exc)))
        return ToolCallDecision(
            receipt.acquired,
            receipt.reason,
            {
                "revision": receipt.record.revision,
                "status": receipt.record.status,
                "attempt_id": receipt.record.attempt_id,
            },
        )

    def complete(
        self,
        *,
        operation_id: str,
        fencing_token: int,
        result_ref: str,
        metadata: dict[str, Any],
    ) -> None:
        self._finish(
            "side_effect_complete",
            operation_id=operation_id,
            fencing_token=fencing_token,
            metadata=metadata,
            result_ref=result_ref,
        )

    def fail(
        self,
        *,
        operation_id: str,
        fencing_token: int,
        reason_code: str,
        uncertain: bool,
        metadata: dict[str, Any],
    ) -> None:
        self._finish(
            "side_effect_uncertain" if uncertain else "side_effect_fail",
            operation_id=operation_id,
            fencing_token=fencing_token,
            metadata=metadata,
            reason_code=reason_code,
        )

    def _finish(
        self,
        command: str,
        *,
        operation_id: str,
        fencing_token: int,
        metadata: dict[str, Any],
        **values: Any,
    ) -> None:
        try:
            response = self._client.command(
                command,
                binding=_metadata_binding(metadata),
                operation_id=operation_id,
                fencing_token=int(fencing_token),
                attempt_id=str(metadata.get("attempt_id") or ""),
                expected_revision=int(metadata.get("expected_revision") or 0),
                tool_id=str(metadata.get("tool_id") or ""),
                side_effect_class=str(metadata.get("side_effect_class") or ""),
                **_tool_approval_values(metadata),
                **values,
            )
            SideEffectGatewayReceipt.from_mapping(response)
        except (WorkflowHubDecisionError, WorkflowWorkerContractError, TypeError, ValueError) as exc:
            raise WorkflowHubDecisionError(
                getattr(exc, "reason_code", "side_effect_gateway_response_invalid")
            ) from exc


class HubProviderRetryBudgetAdapter:
    def __init__(self, client: HttpWorkflowHubDecisionClient) -> None:
        self._client = client

    def consume(
        self,
        *,
        context: ProviderInvocationContext,
        retry_id: str,
        maximum: int,
    ) -> tuple[bool, str, int, int]:
        try:
            response = self._client.command(
                "consume_retry",
                binding=_provider_binding(context),
                retry_id=str(retry_id),
                retry_category="provider",
                maximum=int(maximum),
                attempt_id=context.attempt_id,
                fencing_token=context.fencing_token,
            )
            receipt = RetryBudgetReceipt.from_mapping(response)
        except (WorkflowHubDecisionError, HubTaskContractError) as exc:
            return False, getattr(exc, "reason_code", str(exc)), 0, max(0, int(maximum))
        return True, "provider_combined_retry_reserved", receipt.used, receipt.remaining


class HubProviderBudgetAdapter:
    """Reserve and reconcile token/cost usage at the Hub across workers."""

    def __init__(self, client: HttpWorkflowHubDecisionClient) -> None:
        self._client = client

    def reserve(
        self,
        *,
        context: ProviderInvocationContext,
        estimated_prompt_tokens: int,
        reservation_id: str = "",
    ) -> ProviderBudgetDecision:
        reserved_tokens = max(0, int(estimated_prompt_tokens)) + int(
            context.max_completion_tokens_per_call
        )
        reserved_cost = (
            (
                reserved_tokens
                * context.estimated_cost_micros_per_1000_tokens
                + 999
            )
            // 1000
            if context.estimated_cost_micros_per_1000_tokens
            else 0
        )
        try:
            response = self._client.command(
                "provider_budget_reserve",
                binding=_provider_binding(context),
                reservation_id=reservation_id,
                maximum_attempts=context.max_attempts,
                maximum_tokens=context.max_total_tokens,
                maximum_cost_micros=context.max_cost_micros,
                reserved_tokens=reserved_tokens,
                reserved_cost_micros=reserved_cost,
                attempt_id=context.attempt_id,
                fencing_token=context.fencing_token,
            )
            receipt = ProviderBudgetReceipt.from_mapping(response)
        except (WorkflowHubDecisionError, WorkflowWorkerContractError) as exc:
            return ProviderBudgetDecision(
                False,
                getattr(exc, "reason_code", str(exc)),
                0,
                0,
                0,
            )
        return ProviderBudgetDecision(
            True,
            receipt.reason_code,
            receipt.attempts,
            receipt.reserved_tokens,
            receipt.reserved_cost_micros,
        )

    def reconcile(
        self,
        *,
        context: ProviderInvocationContext,
        reserved_tokens: int,
        actual_total_tokens: int | None,
        reservation_id: str = "",
    ) -> None:
        del reserved_tokens
        if actual_total_tokens is None:
            return
        try:
            response = self._client.command(
                "provider_budget_reconcile",
                binding=_provider_binding(context),
                reservation_id=reservation_id,
                actual_total_tokens=int(actual_total_tokens),
                attempt_id=context.attempt_id,
                fencing_token=context.fencing_token,
            )
            ProviderBudgetReceipt.from_mapping(response)
        except (WorkflowHubDecisionError, WorkflowWorkerContractError) as exc:
            raise ProviderInvocationBlocked(
                getattr(exc, "reason_code", "provider_budget_reconciliation_failed")
            ) from exc


def _tool_binding(request: ToolCallRequest) -> dict[str, Any]:
    return {
        "tenant_id": request.tenant_id,
        "workflow_id": request.workflow_id,
        "run_id": request.run_id,
        "step_id": request.step_id,
        "plan_hash": request.plan_hash,
        "policy_version": request.policy_version,
        "authorization_envelope": dict(request.authorization_envelope),
        "correlation_id": request.correlation_id,
    }


def _metadata_binding(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tenant_id": str(metadata.get("tenant_id") or ""),
        "workflow_id": str(metadata.get("workflow_id") or ""),
        "run_id": str(metadata.get("run_id") or ""),
        "step_id": str(metadata.get("step_id") or ""),
        "plan_hash": str(metadata.get("plan_hash") or ""),
        "policy_version": str(metadata.get("policy_version") or ""),
        "authorization_envelope": dict(metadata.get("authorization_envelope") or {}),
        "correlation_id": str(metadata.get("correlation_id") or ""),
    }


def _provider_binding(context: ProviderInvocationContext) -> dict[str, Any]:
    return {
        "tenant_id": context.tenant_id,
        "workflow_id": context.workflow_id,
        "run_id": context.run_id,
        "step_id": context.step_id,
        "plan_hash": context.plan_hash,
        "policy_version": context.policy_version,
        "authorization_envelope": dict(context.authorization_envelope),
        "correlation_id": context.correlation_id,
    }


def _tool_command_values(
    request: ToolCallRequest,
    descriptor: WorkerToolEntry,
) -> dict[str, Any]:
    return {
        "operation_id": request.resolved_operation_id(),
        "attempt_id": request.attempt_id,
        "fencing_token": request.fencing_token,
        "tool_id": request.tool_id,
        "side_effect_class": descriptor.side_effect_class,
        "approval_ref": request.approval_ref or "",
        "hub_task_id": request.hub_task_id,
        "goal_id": request.goal_id,
        "arguments": dict(request.arguments),
    }


def _tool_approval_values(metadata: Mapping[str, Any]) -> dict[str, Any]:
    arguments = metadata.get("arguments")
    if not isinstance(arguments, Mapping):
        arguments = {}
    return {
        "approval_ref": str(metadata.get("approval_ref") or ""),
        "hub_task_id": str(metadata.get("hub_task_id") or ""),
        "goal_id": str(metadata.get("goal_id") or ""),
        "arguments": dict(arguments),
    }


def _read_token_file(raw_path: str) -> str:
    path = Path(str(raw_path or ""))
    if not path.is_absolute():
        raise ValueError("workflow Hub token file must be absolute")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise ValueError("workflow Hub token file cannot be inspected") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError("workflow Hub token file is unsafe")
    try:
        with path.open("rb") as handle:
            raw = handle.read(16_385)
        token = raw.decode("utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError("workflow Hub token file cannot be read") from exc
    if (
        not 32 <= len(token.encode("utf-8")) <= 16_384
        or "\x00" in token
        or any(character.isspace() for character in token)
    ):
        raise ValueError("workflow Hub token file is invalid")
    return token


def _http_error_reason(error: urllib.error.HTTPError) -> str:
    try:
        raw = error.read(16_385)
        if len(raw) > 16_384:
            return f"workflow_hub_http_{error.code}"
        decoded = json.loads(raw.decode("utf-8"))
        data = decoded.get("data") if isinstance(decoded, Mapping) else None
        reason = str(data.get("reason_code") or "") if isinstance(data, Mapping) else ""
    except (OSError, UnicodeError, json.JSONDecodeError):
        return f"workflow_hub_http_{error.code}"
    if (
        not reason
        or len(reason) > 256
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_:-" for character in reason)
    ):
        return f"workflow_hub_http_{error.code}"
    return reason


__all__ = [
    "HttpWorkflowHubDecisionClient",
    "HubExecutionAuthorizationAdapter",
    "HubProviderBudgetAdapter",
    "HubProviderRetryBudgetAdapter",
    "HubSideEffectLedgerAdapter",
    "HubToolAuthorizationAdapter",
    "WorkflowHubDecisionError",
]
