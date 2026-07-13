"""Wire contracts for worker-to-Hub retry and side-effect decisions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ananta_contracts.hub_task_gateway import RETRY_CATEGORIES

WORKFLOW_WORKER_COMMAND_SCHEMA = "ananta.workflow-runtime-worker-command.v1"
SIDE_EFFECT_GATEWAY_RECEIPT_SCHEMA = "ananta.side-effect-gateway-receipt.v1"
WORKFLOW_WORKER_DECISION_SCHEMA = "ananta.workflow-runtime-worker-decision.v1"
PROVIDER_BUDGET_RECEIPT_SCHEMA = "ananta.provider-budget-receipt.v1"
WORKFLOW_WORKER_COMMANDS = frozenset(
    {
        "authorize_execution",
        "authorize_tool",
        "consume_retry",
        "native_side_effect_claim",
        "native_side_effect_complete",
        "native_side_effect_fail",
        "native_side_effect_uncertain",
        "provider_budget_reconcile",
        "provider_budget_reserve",
        "side_effect_claim",
        "side_effect_complete",
        "side_effect_fail",
        "side_effect_uncertain",
    }
)


class WorkflowWorkerContractError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class WorkflowWorkerBinding:
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    plan_hash: str
    policy_version: str
    authorization_envelope: dict[str, Any]
    correlation_id: str = ""

    @classmethod
    def from_mapping(cls, raw: object) -> "WorkflowWorkerBinding":
        if not isinstance(raw, Mapping):
            raise WorkflowWorkerContractError("workflow_worker_binding_required")
        envelope = raw.get("authorization_envelope")
        if not isinstance(envelope, Mapping):
            raise WorkflowWorkerContractError("workflow_worker_authorization_required")
        value = cls(
            tenant_id=str(raw.get("tenant_id") or "").strip(),
            workflow_id=str(raw.get("workflow_id") or "").strip(),
            run_id=str(raw.get("run_id") or "").strip(),
            step_id=str(raw.get("step_id") or "").strip(),
            plan_hash=str(raw.get("plan_hash") or "").strip(),
            policy_version=str(raw.get("policy_version") or "").strip(),
            authorization_envelope=dict(envelope),
            correlation_id=str(raw.get("correlation_id") or "").strip(),
        )
        value.validate()
        return value

    def validate(self) -> None:
        required = (
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.step_id,
            self.plan_hash,
            self.policy_version,
        )
        if any(not item or len(item) > 256 for item in required):
            raise WorkflowWorkerContractError("workflow_worker_binding_invalid")
        if re.fullmatch(r"(?:sha256:)?[a-fA-F0-9]{64}", self.plan_hash) is None:
            raise WorkflowWorkerContractError("workflow_worker_plan_hash_invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "plan_hash": self.plan_hash,
            "policy_version": self.policy_version,
            "authorization_envelope": dict(self.authorization_envelope),
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True)
class SideEffectGatewayRecord:
    operation_id: str
    status: str
    revision: int
    fencing_token: int
    attempt_id: str

    @classmethod
    def from_mapping(cls, raw: object) -> "SideEffectGatewayRecord":
        if not isinstance(raw, Mapping):
            raise WorkflowWorkerContractError("side_effect_gateway_record_invalid")
        try:
            value = cls(
                operation_id=str(raw.get("operation_id") or ""),
                status=str(raw.get("status") or ""),
                revision=int(raw.get("revision")),
                fencing_token=int(raw.get("fencing_token")),
                attempt_id=str(raw.get("attempt_id") or ""),
            )
        except (TypeError, ValueError) as exc:
            raise WorkflowWorkerContractError("side_effect_gateway_record_invalid") from exc
        if not value.operation_id or value.revision < 1 or value.fencing_token < 0:
            raise WorkflowWorkerContractError("side_effect_gateway_record_invalid")
        return value


@dataclass(frozen=True)
class SideEffectGatewayReceipt:
    record: SideEffectGatewayRecord
    acquired: bool
    reason: str
    schema: str = SIDE_EFFECT_GATEWAY_RECEIPT_SCHEMA

    @classmethod
    def from_mapping(cls, raw: object) -> "SideEffectGatewayReceipt":
        if (
            not isinstance(raw, Mapping)
            or str(raw.get("schema") or "") != SIDE_EFFECT_GATEWAY_RECEIPT_SCHEMA
        ):
            raise WorkflowWorkerContractError("side_effect_gateway_receipt_invalid")
        return cls(
            record=SideEffectGatewayRecord.from_mapping(raw.get("record")),
            acquired=bool(raw.get("acquired", False)),
            reason=str(raw.get("reason") or ""),
        )


@dataclass(frozen=True)
class ProviderBudgetReceipt:
    reservation_id: str
    attempts: int
    tokens: int
    cost_micros: int
    reserved_tokens: int
    reserved_cost_micros: int
    maximum_attempts: int
    maximum_tokens: int
    maximum_cost_micros: int
    reconciled: bool
    reason_code: str
    schema: str = PROVIDER_BUDGET_RECEIPT_SCHEMA

    @classmethod
    def from_mapping(cls, raw: object) -> "ProviderBudgetReceipt":
        if (
            not isinstance(raw, Mapping)
            or str(raw.get("schema") or "") != PROVIDER_BUDGET_RECEIPT_SCHEMA
        ):
            raise WorkflowWorkerContractError("provider_budget_receipt_invalid")
        try:
            value = cls(
                reservation_id=str(raw.get("reservation_id") or ""),
                attempts=int(str(raw.get("attempts"))),
                tokens=int(str(raw.get("tokens"))),
                cost_micros=int(str(raw.get("cost_micros"))),
                reserved_tokens=int(str(raw.get("reserved_tokens"))),
                reserved_cost_micros=int(str(raw.get("reserved_cost_micros"))),
                maximum_attempts=int(str(raw.get("maximum_attempts"))),
                maximum_tokens=int(str(raw.get("maximum_tokens"))),
                maximum_cost_micros=int(str(raw.get("maximum_cost_micros"))),
                reconciled=bool(raw.get("reconciled", False)),
                reason_code=str(raw.get("reason_code") or ""),
            )
        except (TypeError, ValueError) as exc:
            raise WorkflowWorkerContractError("provider_budget_receipt_invalid") from exc
        if (
            not value.reservation_id
            or value.attempts < 1
            or value.maximum_attempts < 1
            or min(
                value.tokens,
                value.cost_micros,
                value.reserved_tokens,
                value.reserved_cost_micros,
                value.maximum_tokens,
                value.maximum_cost_micros,
            )
            < 0
        ):
            raise WorkflowWorkerContractError("provider_budget_receipt_invalid")
        return value


def validate_retry_category(category: str) -> str:
    value = str(category or "")
    if value not in RETRY_CATEGORIES:
        raise WorkflowWorkerContractError("workflow_retry_category_invalid")
    return value


__all__ = [
    "PROVIDER_BUDGET_RECEIPT_SCHEMA",
    "SIDE_EFFECT_GATEWAY_RECEIPT_SCHEMA",
    "WORKFLOW_WORKER_COMMAND_SCHEMA",
    "WORKFLOW_WORKER_COMMANDS",
    "WORKFLOW_WORKER_DECISION_SCHEMA",
    "SideEffectGatewayReceipt",
    "SideEffectGatewayRecord",
    "WorkflowWorkerBinding",
    "ProviderBudgetReceipt",
    "WorkflowWorkerContractError",
    "validate_retry_category",
]
