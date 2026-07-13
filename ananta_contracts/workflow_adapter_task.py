"""Versioned Hub-task contract consumed by framework adapter workers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ananta_contracts.provider_execution import ProviderExecutionBinding
from ananta_contracts.temporal_workflow import AuthorizationEnvelopeRef
from ananta_contracts.workflow_worker_gateway import WorkflowWorkerBinding

WORKFLOW_ADAPTER_TASK_SCHEMA = "ananta.workflow-adapter-worker-task.v1"
WORKFLOW_ADAPTER_TASK_RESULT_SCHEMA = "ananta.workflow-adapter-worker-result.v1"
WORKFLOW_ADAPTER_TASK_VERIFICATION_SCHEMA = "ananta.workflow-adapter-task-verification.v1"
WORKFLOW_ADAPTER_RUNTIME_PATH = "workflow_adapter"
WORKFLOW_ADAPTER_KINDS = frozenset({"langgraph"})
WORKFLOW_ADAPTER_COMMANDS = frozenset({"dry_run", "execute"})


class WorkflowAdapterTaskContractError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class WorkflowAdapterTask:
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    plan_hash: str
    policy_version: str
    adapter_kind: str
    command: str
    task_type: str
    attempt_id: str
    fencing_token: int
    authorization_envelope: AuthorizationEnvelopeRef
    payload: dict[str, Any]
    correlation_id: str = ""
    provider_binding: ProviderExecutionBinding | None = None
    runtime_path: str = WORKFLOW_ADAPTER_RUNTIME_PATH
    schema: str = WORKFLOW_ADAPTER_TASK_SCHEMA

    @classmethod
    def from_mapping(cls, raw: object) -> "WorkflowAdapterTask":
        if not isinstance(raw, Mapping):
            raise WorkflowAdapterTaskContractError("workflow_adapter_task_required")
        try:
            value = cls(
                schema=str(raw.get("schema") or ""),
                tenant_id=str(raw.get("tenant_id") or "").strip(),
                workflow_id=str(raw.get("workflow_id") or "").strip(),
                run_id=str(raw.get("run_id") or "").strip(),
                step_id=str(raw.get("step_id") or "").strip(),
                plan_hash=str(raw.get("plan_hash") or "").strip(),
                policy_version=str(raw.get("policy_version") or "").strip(),
                adapter_kind=str(raw.get("adapter_kind") or "").strip().lower(),
                command=str(raw.get("command") or "").strip().lower(),
                task_type=str(raw.get("task_type") or "").strip(),
                attempt_id=str(raw.get("attempt_id") or "").strip(),
                fencing_token=int(raw.get("fencing_token")),
                authorization_envelope=AuthorizationEnvelopeRef.from_mapping(
                    raw.get("authorization_envelope")
                ),
                payload=dict(raw.get("payload") or {}),
                correlation_id=str(raw.get("correlation_id") or "").strip(),
                provider_binding=(
                    ProviderExecutionBinding.from_mapping(raw["provider_binding"])
                    if raw.get("provider_binding") is not None
                    else None
                ),
                runtime_path=str(raw.get("runtime_path") or "").strip(),
            )
        except (TypeError, ValueError) as exc:
            reason = getattr(exc, "reason_code", "workflow_adapter_task_invalid")
            raise WorkflowAdapterTaskContractError(str(reason)) from exc
        value.validate()
        return value

    def validate(self) -> None:
        if self.schema != WORKFLOW_ADAPTER_TASK_SCHEMA:
            raise WorkflowAdapterTaskContractError("workflow_adapter_task_schema_unsupported")
        if self.runtime_path != WORKFLOW_ADAPTER_RUNTIME_PATH:
            raise WorkflowAdapterTaskContractError("workflow_adapter_runtime_path_mismatch")
        required = (
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.step_id,
            self.plan_hash,
            self.policy_version,
            self.task_type,
            self.attempt_id,
        )
        if any(not item or len(item) > 256 or "\x00" in item for item in required):
            raise WorkflowAdapterTaskContractError("workflow_adapter_task_binding_invalid")
        if self.adapter_kind not in WORKFLOW_ADAPTER_KINDS:
            raise WorkflowAdapterTaskContractError("workflow_adapter_kind_unsupported")
        if self.command not in WORKFLOW_ADAPTER_COMMANDS:
            raise WorkflowAdapterTaskContractError("workflow_adapter_command_unsupported")
        if self.command == "execute" and self.provider_binding is None:
            raise WorkflowAdapterTaskContractError(
                "workflow_adapter_provider_binding_required"
            )
        if self.command == "dry_run" and self.provider_binding is not None:
            raise WorkflowAdapterTaskContractError(
                "workflow_adapter_dry_run_provider_transport_denied"
            )
        if self.provider_binding is not None:
            try:
                self.provider_binding.validate()
            except ValueError as exc:
                raise WorkflowAdapterTaskContractError(
                    "workflow_adapter_provider_binding_invalid"
                ) from exc
        if self.fencing_token < 1:
            raise WorkflowAdapterTaskContractError("workflow_adapter_fencing_invalid")
        try:
            rendered = json.dumps(
                self.payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise WorkflowAdapterTaskContractError("workflow_adapter_payload_invalid") from exc
        if len(rendered) > 262_144:
            raise WorkflowAdapterTaskContractError("workflow_adapter_payload_too_large")
        if _contains_forbidden_secret_key(self.payload):
            raise WorkflowAdapterTaskContractError("workflow_adapter_embedded_secret_denied")
        try:
            self.authorization_envelope.validate_binding(
                tenant_id=self.tenant_id,
                workflow_id=self.workflow_id,
                run_id=self.run_id,
                step_id=self.step_id,
                plan_hash=self.plan_hash,
            )
        except Exception as exc:
            reason = getattr(exc, "reason_code", "workflow_adapter_authorization_binding_invalid")
            raise WorkflowAdapterTaskContractError(str(reason)) from exc
        if self.authorization_envelope.policy_version != self.policy_version:
            raise WorkflowAdapterTaskContractError("workflow_adapter_policy_binding_mismatch")
        provider_context = self.payload.get("provider_context")
        if not isinstance(provider_context, Mapping):
            raise WorkflowAdapterTaskContractError(
                "workflow_adapter_provider_context_required"
            )
        selected_provider = str(
            provider_context.get("selected_provider_id") or ""
        ).strip()
        selected_model = str(provider_context.get("selected_model_id") or "").strip()
        if self.provider_binding is None:
            if selected_provider or selected_model or bool(
                provider_context.get("require_hub_provider_budget", False)
            ):
                raise WorkflowAdapterTaskContractError(
                    "workflow_adapter_provider_transport_binding_mismatch"
                )
        elif (
            selected_provider != self.provider_binding.provider_id
            or selected_model != self.provider_binding.model_id
            or str(provider_context.get("provider_binding_id") or "")
            != self.provider_binding.binding_id
            or not bool(provider_context.get("require_hub_provider_budget", False))
        ):
            raise WorkflowAdapterTaskContractError(
                "workflow_adapter_provider_binding_mismatch"
            )

    def worker_payload(self) -> dict[str, Any]:
        """Overlay Hub bindings so nested payload values cannot widen scope."""

        payload = dict(self.payload)
        provider_context = dict(payload.get("provider_context") or {})
        if self.provider_binding is not None:
            provider_context.update(
                {
                    "provider_binding_id": self.provider_binding.binding_id,
                    "selected_provider_id": self.provider_binding.provider_id,
                    "selected_model_id": self.provider_binding.model_id,
                    "require_hub_provider_budget": True,
                }
            )
        payload["provider_context"] = provider_context
        return {
            **payload,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "plan_hash": self.plan_hash,
            "policy_version": self.policy_version,
            "correlation_id": self.correlation_id,
            "attempt_id": self.attempt_id,
            "fencing_token": self.fencing_token,
            "authorization_envelope": self.authorization_envelope.to_dict(),
        }

    def worker_binding(self) -> WorkflowWorkerBinding:
        return WorkflowWorkerBinding.from_mapping(
            {
                "tenant_id": self.tenant_id,
                "workflow_id": self.workflow_id,
                "run_id": self.run_id,
                "step_id": self.step_id,
                "plan_hash": self.plan_hash,
                "policy_version": self.policy_version,
                "authorization_envelope": self.authorization_envelope.to_dict(),
                "correlation_id": self.correlation_id,
            }
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


@dataclass(frozen=True)
class WorkflowAdapterTaskResult:
    hub_task_id: str
    adapter_kind: str
    status: str
    reason_code: str = ""
    artifacts: tuple[dict[str, Any], ...] = ()
    sources: tuple[dict[str, Any], ...] = ()
    summary: str = ""
    adapter_result: dict[str, Any] | None = None
    schema: str = WORKFLOW_ADAPTER_TASK_RESULT_SCHEMA

    @classmethod
    def from_mapping(cls, raw: object) -> "WorkflowAdapterTaskResult":
        if not isinstance(raw, Mapping):
            raise WorkflowAdapterTaskContractError("workflow_adapter_result_invalid")
        artifacts = raw.get("artifacts") or ()
        sources = raw.get("sources") or ()
        adapter_result = raw.get("adapter_result")
        if (
            isinstance(artifacts, (str, bytes))
            or not isinstance(artifacts, (list, tuple))
            or any(not isinstance(item, Mapping) for item in artifacts)
            or isinstance(sources, (str, bytes))
            or not isinstance(sources, (list, tuple))
            or any(not isinstance(item, Mapping) for item in sources)
            or (adapter_result is not None and not isinstance(adapter_result, Mapping))
        ):
            raise WorkflowAdapterTaskContractError("workflow_adapter_result_invalid")
        value = cls(
            schema=str(raw.get("schema") or ""),
            hub_task_id=str(raw.get("hub_task_id") or "").strip(),
            adapter_kind=str(raw.get("adapter_kind") or "").strip().lower(),
            status=str(raw.get("status") or "").strip().lower(),
            reason_code=str(raw.get("reason_code") or "").strip(),
            summary=str(raw.get("summary") or ""),
            artifacts=tuple(dict(item) for item in artifacts),
            sources=tuple(dict(item) for item in sources),
            adapter_result=(dict(adapter_result) if adapter_result is not None else None),
        )
        value.validate()
        return value

    def validate(self) -> None:
        if self.schema != WORKFLOW_ADAPTER_TASK_RESULT_SCHEMA or not self.hub_task_id:
            raise WorkflowAdapterTaskContractError("workflow_adapter_result_invalid")
        if self.adapter_kind not in WORKFLOW_ADAPTER_KINDS | {"native"}:
            raise WorkflowAdapterTaskContractError("workflow_adapter_result_kind_invalid")
        if self.status not in {"success", "blocked", "failed", "cancelled", "unsupported"}:
            raise WorkflowAdapterTaskContractError("workflow_adapter_result_status_invalid")
        if len(self.reason_code) > 256 or len(self.summary.encode("utf-8")) > 16_384:
            raise WorkflowAdapterTaskContractError("workflow_adapter_result_invalid")
        payload = {
            "artifacts": list(self.artifacts),
            "sources": list(self.sources),
            "adapter_result": self.adapter_result,
        }
        try:
            rendered = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise WorkflowAdapterTaskContractError("workflow_adapter_result_invalid") from exc
        if len(rendered) > 1_048_576 or _contains_forbidden_secret_key(payload):
            raise WorkflowAdapterTaskContractError("workflow_adapter_result_unsafe")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.schema,
            "hub_task_id": self.hub_task_id,
            "adapter_kind": self.adapter_kind,
            "status": self.status,
            "reason_code": self.reason_code,
            "summary": self.summary,
            "artifacts": [dict(item) for item in self.artifacts],
            "sources": [dict(item) for item in self.sources],
            "adapter_result": dict(self.adapter_result) if self.adapter_result is not None else None,
        }

    def verification_update(self) -> dict[str, Any]:
        return {
            "schema": WORKFLOW_ADAPTER_TASK_VERIFICATION_SCHEMA,
            "workflow_adapter_task_result": self.to_dict(),
        }


__all__ = [
    "WORKFLOW_ADAPTER_COMMANDS",
    "WORKFLOW_ADAPTER_KINDS",
    "WORKFLOW_ADAPTER_TASK_RESULT_SCHEMA",
    "WORKFLOW_ADAPTER_TASK_SCHEMA",
    "WORKFLOW_ADAPTER_TASK_VERIFICATION_SCHEMA",
    "WORKFLOW_ADAPTER_RUNTIME_PATH",
    "WorkflowAdapterTask",
    "WorkflowAdapterTaskContractError",
    "WorkflowAdapterTaskResult",
]
