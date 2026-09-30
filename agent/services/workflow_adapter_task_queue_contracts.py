"""Public contracts of the Hub workflow-adapter task queue.

Submission and receipt value types, the queue error, the provided queue
port and the pure submission validation rules.  Callers that only submit or
control adapter tasks depend on this module (or the re-exporting service
module) without pulling in the queue implementation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent.services.model_routing_contract import (
    ModelRoutingConfig,
    ModelRoutingContractError,
)
from ananta_contracts.provider_execution import (
    ProviderExecutionBinding,
    ProviderExecutionBindingError,
    ProviderProfileAttemptPlanEntry,
    ProviderProfileExecutionBinding,
)

WORKFLOW_ADAPTER_RECEIPT_SCHEMA = "ananta.workflow-adapter-task-receipt.v1"
WORKFLOW_ADAPTER_STATUS_SCHEMA = "ananta.workflow-adapter-task-status.v1"
WORKFLOW_ADAPTER_CONTROL_SCHEMA = "ananta.workflow-adapter-control.v1"


class WorkflowAdapterQueueError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = str(reason_code or "workflow_adapter_queue_failed")
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class WorkflowAdapterTaskSubmission:
    tenant_id: str
    subject_id: str
    workflow_id: str
    run_id: str
    step_id: str
    plan_hash: str
    policy_version: str
    adapter_kind: str
    command: str
    task_type: str
    payload: dict[str, Any]
    allowed_tools: tuple[str, ...] = ()
    allowed_artifacts: tuple[str, ...] = ()
    correlation_id: str = ""
    idempotency_key: str = ""
    maximum_retries: int = 0
    max_total_tokens: int = 0
    max_cost_micros: int = 0
    authorization_ttl_seconds: float = 1800.0
    provider_binding: ProviderExecutionBinding | None = None
    provider_decision_reason: str = ""
    primary_profile_id: str = ""
    provider_profile_bindings: tuple[ProviderProfileExecutionBinding, ...] = ()
    provider_attempt_plan: tuple[ProviderProfileAttemptPlanEntry, ...] = ()
    provider_maximum_attempts: int = 0
    model_routing: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        identifiers = (
            self.tenant_id,
            self.subject_id,
            self.workflow_id,
            self.run_id,
            self.step_id,
            self.plan_hash,
            self.policy_version,
            self.task_type,
            self.idempotency_key,
        )
        if any(not value or len(value) > 256 or "\x00" in value for value in identifiers):
            raise WorkflowAdapterQueueError(
                "workflow_adapter_submission_binding_invalid", status_code=422
            )
        if re.fullmatch(r"(?:sha256:)?[a-fA-F0-9]{64}", self.plan_hash) is None:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_plan_hash_invalid", status_code=422
            )
        if self.correlation_id and (
            len(self.correlation_id) > 256 or "\x00" in self.correlation_id
        ):
            raise WorkflowAdapterQueueError(
                "workflow_adapter_correlation_id_invalid", status_code=422
            )
        for values, reason in (
            (self.allowed_tools, "workflow_adapter_allowed_tools_invalid"),
            (self.allowed_artifacts, "workflow_adapter_allowed_artifacts_invalid"),
        ):
            if len(values) > 128 or any(
                not value or len(value) > 256 or "\x00" in value for value in values
            ):
                raise WorkflowAdapterQueueError(reason, status_code=422)
        if self.adapter_kind != "langgraph":
            raise WorkflowAdapterQueueError(
                "workflow_adapter_kind_unsupported", status_code=422
            )
        if self.command not in {"dry_run", "execute"}:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_command_unsupported", status_code=422
            )
        if self.command == "execute" and self.provider_binding is None:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_provider_selection_required", status_code=503
            )
        if self.command == "dry_run" and self.provider_binding is not None:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_dry_run_provider_transport_denied", status_code=422
            )
        if self.provider_binding is not None:
            try:
                self.provider_binding.validate()
            except ValueError as exc:
                raise WorkflowAdapterQueueError(
                    "workflow_adapter_provider_selection_invalid", status_code=422
                ) from exc
        try:
            _validate_provider_profile_bindings(
                command=self.command,
                provider_binding=self.provider_binding,
                primary_profile_id=self.primary_profile_id,
                profile_bindings=self.provider_profile_bindings,
                provider_attempt_plan=self.provider_attempt_plan,
                provider_maximum_attempts=self.provider_maximum_attempts,
            )
        except ProviderExecutionBindingError as exc:
            raise WorkflowAdapterQueueError(
                exc.reason_code,
                status_code=422,
            ) from exc
        if self.model_routing:
            try:
                ModelRoutingConfig.assert_runtime_mapping(
                    self.model_routing
                )
            except (ModelRoutingContractError, ValueError) as exc:
                raise WorkflowAdapterQueueError(
                    "workflow_adapter_model_routing_invalid",
                    status_code=422,
                ) from exc
        if (
            not self.provider_decision_reason
            or len(self.provider_decision_reason) > 256
            or "\x00" in self.provider_decision_reason
        ):
            raise WorkflowAdapterQueueError(
                "workflow_adapter_provider_decision_reason_required", status_code=422
            )
        if self.maximum_retries < 0 or self.maximum_retries > 32:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_retry_budget_invalid", status_code=422
            )
        if not 0 <= self.max_total_tokens <= 10_000_000:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_token_budget_invalid", status_code=422
            )
        if not 0 <= self.max_cost_micros <= 10_000_000_000:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_cost_budget_invalid", status_code=422
            )
        if not 60 <= self.authorization_ttl_seconds <= 86_400:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_authorization_ttl_invalid", status_code=422
            )
        try:
            rendered = json.dumps(
                self.payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_payload_invalid", status_code=422
            ) from exc
        if len(rendered) > 262_144:
            raise WorkflowAdapterQueueError(
                "workflow_adapter_payload_too_large", status_code=413
            )
        if _contains_forbidden_secret_key(self.payload):
            raise WorkflowAdapterQueueError(
                "workflow_adapter_embedded_secret_denied", status_code=422
            )


@dataclass(frozen=True)
class WorkflowAdapterTaskReceipt:
    hub_task_id: str
    workflow_id: str
    run_id: str
    step_id: str
    operation_id: str
    adapter_kind: str
    command: str
    accepted: bool
    duplicate: bool = False
    status: str = "created"
    reason_code: str = ""
    provider_binding: ProviderExecutionBinding | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKFLOW_ADAPTER_RECEIPT_SCHEMA,
            "hub_task_id": self.hub_task_id,
            "operation_id": self.operation_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "adapter_kind": self.adapter_kind,
            "command": self.command,
            "accepted": self.accepted,
            "duplicate": self.duplicate,
            "status": self.status,
            "reason_code": self.reason_code,
            "provider_binding": (
                self.provider_binding.to_dict() if self.provider_binding else None
            ),
        }


class WorkflowAdapterTaskQueuePort(Protocol):
    def submit(self, submission: WorkflowAdapterTaskSubmission) -> WorkflowAdapterTaskReceipt: ...

    def status(
        self, *, tenant_id: str, subject_id: str, hub_task_id: str
    ) -> dict[str, Any]: ...

    def inspect(
        self, *, tenant_id: str, subject_id: str, hub_task_id: str
    ) -> dict[str, Any]: ...

    def cancel(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        hub_task_id: str,
        reason: str,
    ) -> dict[str, Any]: ...

    def history(
        self, *, tenant_id: str, subject_id: str, hub_task_id: str
    ) -> tuple[dict[str, Any], ...]: ...


def _validate_provider_profile_bindings(
    *,
    command: str,
    provider_binding: ProviderExecutionBinding | None,
    primary_profile_id: str,
    profile_bindings: tuple[ProviderProfileExecutionBinding, ...],
    provider_attempt_plan: tuple[ProviderProfileAttemptPlanEntry, ...],
    provider_maximum_attempts: int,
) -> None:
    if len(profile_bindings) > 8:
        raise ProviderExecutionBindingError(
            "provider_profile_binding_limit_exceeded"
        )
    if command == "dry_run":
        if (
            primary_profile_id
            or profile_bindings
            or provider_attempt_plan
            or provider_maximum_attempts
        ):
            raise ProviderExecutionBindingError(
                "workflow_adapter_dry_run_provider_transport_denied"
            )
        return
    if not profile_bindings:
        if (
            primary_profile_id
            or provider_attempt_plan
            or provider_maximum_attempts
        ):
            raise ProviderExecutionBindingError(
                "provider_primary_profile_binding_missing"
            )
        return
    if provider_binding is None or not primary_profile_id:
        raise ProviderExecutionBindingError(
            "provider_primary_profile_binding_missing"
        )
    if (
        len(provider_attempt_plan) != len(profile_bindings)
        or sum(item.maximum_attempts for item in provider_attempt_plan)
        != provider_maximum_attempts
        or not len(profile_bindings) <= provider_maximum_attempts <= 33
    ):
        raise ProviderExecutionBindingError(
            "provider_profile_retry_budget_invalid"
        )
    seen: set[str] = set()
    primary: ProviderExecutionBinding | None = None
    for item in profile_bindings:
        if not isinstance(item, ProviderProfileExecutionBinding):
            raise ProviderExecutionBindingError(
                "provider_profile_binding_invalid"
            )
        item.validate()
        if item.profile_id in seen:
            raise ProviderExecutionBindingError(
                "provider_profile_binding_duplicate"
            )
        seen.add(item.profile_id)
        if item.profile_id == primary_profile_id:
            primary = item.binding
    if primary != provider_binding:
        raise ProviderExecutionBindingError(
            "provider_primary_profile_binding_mismatch"
        )
    expected = {
        item.profile_id: item.binding
        for item in profile_bindings
    }
    if tuple(item.profile_id for item in provider_attempt_plan) != tuple(
        item.profile_id for item in profile_bindings
    ):
        raise ProviderExecutionBindingError(
            "provider_attempt_plan_order_mismatch"
        )
    for item in provider_attempt_plan:
        item.validate()
        binding = expected.get(item.profile_id)
        if (
            binding is None
            or item.binding_id != binding.binding_id
            or item.provider_id != binding.provider_id
            or item.model_id != binding.model_id
        ):
            raise ProviderExecutionBindingError(
                "provider_attempt_plan_binding_mismatch"
            )


def _contains_forbidden_secret_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            if key in {
                "api_key",
                "authorization",
                "cookie",
                "credential",
                "password",
                "private_key",
                "secret",
                "token",
            } or any(marker in key for marker in ("password", "private_key")):
                if not key.endswith("_ref"):
                    return True
            if _contains_forbidden_secret_key(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_secret_key(item) for item in value)
    return False


__all__ = [
    "WORKFLOW_ADAPTER_CONTROL_SCHEMA",
    "WORKFLOW_ADAPTER_RECEIPT_SCHEMA",
    "WORKFLOW_ADAPTER_STATUS_SCHEMA",
    "WorkflowAdapterQueueError",
    "WorkflowAdapterTaskQueuePort",
    "WorkflowAdapterTaskReceipt",
    "WorkflowAdapterTaskSubmission",
]
