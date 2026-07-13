"""Runtime-neutral wire contracts for one Hub-delegated Native graph node."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent.services.workflow_runtime._serialization import contains_sensitive_keys, redact_json
from agent.services.workflow_runtime.execution_plan import ExecutionNode
from agent.services.workflow_runtime.security import RuntimeAuthorizationEnvelope
from ananta_contracts.provider_execution import ProviderExecutionBinding

NATIVE_NODE_COMMAND_SCHEMA = "ananta.native_node_command.v1"
NATIVE_NODE_RESULT_SCHEMA = "ananta.native_node_result.v1"
_REASON_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:,-]{0,159}$")


@dataclass(frozen=True)
class NativeNodeCommand:
    command_id: str
    control_task_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    plan_hash: str
    policy_version: str
    node: ExecutionNode
    authorization: RuntimeAuthorizationEnvelope
    attempt_id: str
    fencing_token: int
    input_data: dict[str, Any] = field(default_factory=dict)
    artifact_refs: dict[str, str] = field(default_factory=dict)
    operation_id: str = ""
    side_effect_revision: int = 0
    provider_binding: ProviderExecutionBinding | None = None
    schema: str = NATIVE_NODE_COMMAND_SCHEMA

    def assert_valid(self) -> None:
        required = (
            self.command_id,
            self.control_task_id,
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.plan_hash,
            self.policy_version,
            self.node.node_id,
            self.attempt_id,
        )
        if any(not value for value in required):
            raise ValueError("native_node_command_binding_required")
        if self.schema != NATIVE_NODE_COMMAND_SCHEMA:
            raise ValueError("native_node_command_schema_unsupported")
        if self.fencing_token < 1:
            raise ValueError("native_node_command_fencing_invalid")
        if contains_sensitive_keys(self.input_data):
            raise ValueError("native_node_command_embedded_secret_denied")
        if native_node_requires_provider(self.node) and self.provider_binding is None:
            raise ValueError("native_node_provider_binding_required")
        if self.provider_binding is not None:
            self.provider_binding.validate()
        if self.node.side_effect_class in {"none", "read"}:
            if self.operation_id or self.side_effect_revision:
                raise ValueError("native_node_command_unexpected_side_effect")
        elif not self.operation_id or self.side_effect_revision < 2:
            raise ValueError("native_node_command_side_effect_authorization_required")

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "NativeNodeCommand":
        value = cls(
            command_id=str(raw.get("command_id") or ""),
            control_task_id=str(raw.get("control_task_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            plan_hash=str(raw.get("plan_hash") or ""),
            policy_version=str(raw.get("policy_version") or ""),
            node=ExecutionNode.from_mapping(dict(raw.get("node") or {})),
            authorization=RuntimeAuthorizationEnvelope.from_mapping(
                dict(raw.get("authorization") or {})
            ),
            attempt_id=str(raw.get("attempt_id") or ""),
            fencing_token=int(raw.get("fencing_token") or 0),
            input_data=dict(raw.get("input_data") or {}),
            artifact_refs={
                str(key): str(item) for key, item in dict(raw.get("artifact_refs") or {}).items()
            },
            operation_id=str(raw.get("operation_id") or ""),
            side_effect_revision=int(raw.get("side_effect_revision") or 0),
            provider_binding=(
                ProviderExecutionBinding.from_mapping(raw["provider_binding"])
                if raw.get("provider_binding") is not None
                else None
            ),
            schema=str(raw.get("schema") or NATIVE_NODE_COMMAND_SCHEMA),
        )
        value.assert_valid()
        return value

    def to_dict(self, *, redacted: bool = False) -> dict[str, Any]:
        self.assert_valid()
        return {
            "schema": self.schema,
            "command_id": self.command_id,
            "control_task_id": self.control_task_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "plan_hash": self.plan_hash,
            "policy_version": self.policy_version,
            "node": self.node.to_dict(),
            "authorization": self.authorization.to_dict(redacted=redacted),
            "attempt_id": self.attempt_id,
            "fencing_token": self.fencing_token,
            "input_data": dict(redact_json(self.input_data)),
            "artifact_refs": dict(sorted(self.artifact_refs.items())),
            "operation_id": self.operation_id,
            "side_effect_revision": self.side_effect_revision,
            "provider_binding": (
                self.provider_binding.to_dict() if self.provider_binding else None
            ),
        }


def native_node_requires_provider(node: ExecutionNode) -> bool:
    """Provider transport is explicit in the Hub-approved plan contract."""

    metadata = dict(node.metadata or {})
    mode = str(metadata.get("provider_transport") or "").strip().lower()
    if mode == "required" or metadata.get("provider_required") is True:
        return True
    return bool(
        set(node.required_capabilities)
        & {"llm", "model_inference", "text_generation"}
    )


@dataclass(frozen=True)
class NativeNodeResult:
    result_id: str
    command_id: str
    hub_task_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    node_id: str
    attempt_id: str
    fencing_token: int
    status: str
    output_data: dict[str, Any] = field(default_factory=dict)
    artifact_refs: dict[str, str] = field(default_factory=dict)
    budget_usage: dict[str, int | float] = field(default_factory=dict)
    reason_code: str = ""
    side_effect_status: str = ""
    schema: str = NATIVE_NODE_RESULT_SCHEMA

    def assert_valid(self) -> None:
        required = (
            self.result_id,
            self.command_id,
            self.hub_task_id,
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.node_id,
            self.attempt_id,
        )
        if any(not value for value in required):
            raise ValueError("native_node_result_binding_required")
        if self.schema != NATIVE_NODE_RESULT_SCHEMA:
            raise ValueError("native_node_result_schema_unsupported")
        if self.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("native_node_result_status_invalid")
        if self.fencing_token < 1:
            raise ValueError("native_node_result_fencing_invalid")
        if contains_sensitive_keys(self.output_data):
            raise ValueError("native_node_result_embedded_secret_denied")
        if self.reason_code and not _REASON_CODE.fullmatch(self.reason_code):
            raise ValueError("native_node_result_reason_code_invalid")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
            for value in self.budget_usage.values()
        ):
            raise ValueError("native_node_result_budget_invalid")

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "NativeNodeResult":
        value = cls(
            result_id=str(raw.get("result_id") or ""),
            command_id=str(raw.get("command_id") or ""),
            hub_task_id=str(raw.get("hub_task_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            node_id=str(raw.get("node_id") or ""),
            attempt_id=str(raw.get("attempt_id") or ""),
            fencing_token=int(raw.get("fencing_token") or 0),
            status=str(raw.get("status") or ""),
            output_data=dict(raw.get("output_data") or {}),
            artifact_refs={
                str(key): str(item) for key, item in dict(raw.get("artifact_refs") or {}).items()
            },
            budget_usage={
                str(key): item for key, item in dict(raw.get("budget_usage") or {}).items()
            },
            reason_code=str(raw.get("reason_code") or ""),
            side_effect_status=str(raw.get("side_effect_status") or ""),
            schema=str(raw.get("schema") or NATIVE_NODE_RESULT_SCHEMA),
        )
        value.assert_valid()
        return value

    def to_dict(self) -> dict[str, Any]:
        self.assert_valid()
        return {
            "schema": self.schema,
            "result_id": self.result_id,
            "command_id": self.command_id,
            "hub_task_id": self.hub_task_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "attempt_id": self.attempt_id,
            "fencing_token": self.fencing_token,
            "status": self.status,
            "output_data": dict(redact_json(self.output_data)),
            "artifact_refs": dict(sorted(self.artifact_refs.items())),
            "budget_usage": dict(sorted(self.budget_usage.items())),
            "reason_code": self.reason_code,
            "side_effect_status": self.side_effect_status,
        }


@dataclass(frozen=True)
class HubTaskReceipt:
    hub_task_id: str
    command_id: str
    accepted: bool
    reason_code: str = ""
