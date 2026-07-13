"""Wire constants for Hub-owned workflow task delegation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

HUB_TASK_COMMAND_SCHEMA = "ananta.temporal-hub-task-command.v1"
HUB_TASK_RECEIPT_SCHEMA = "ananta.temporal-hub-task-receipt.v1"
RETRY_BUDGET_RECEIPT_SCHEMA = "ananta.workflow-retry-budget-receipt.v1"
RETRY_CATEGORIES = frozenset(
    {
        "temporal_activity",
        "hub_task",
        "worker",
        "tool",
        "provider",
    }
)
HUB_TASK_STATUSES = frozenset(
    {
        "created",
        "delegated",
        "assigned",
        "running",
        "completed",
        "failed",
        "cancelled",
        "uncertain",
    }
)
HUB_LEDGER_STATES = frozenset({"authorized", "started", "completed", "failed", "uncertain"})


class HubTaskContractError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class HubTaskReceipt:
    hub_task_id: str
    operation_id: str
    status: str
    authorization_state: str
    ledger_state: str
    artifact_refs: tuple[Mapping[str, Any], ...] = ()
    canonical_event_refs: tuple[str, ...] = ()
    checkpoint_ref: str = ""
    reason_code: str = ""
    schema: str = HUB_TASK_RECEIPT_SCHEMA

    @classmethod
    def from_mapping(cls, raw: object) -> "HubTaskReceipt":
        if not isinstance(raw, Mapping) or str(raw.get("schema") or "") != HUB_TASK_RECEIPT_SCHEMA:
            raise HubTaskContractError("invalid_hub_gateway_response")
        artifacts = raw.get("artifact_refs")
        events = raw.get("canonical_event_refs")
        if artifacts is None:
            artifacts = []
        if events is None:
            events = []
        if not isinstance(artifacts, list) or not all(isinstance(item, Mapping) for item in artifacts):
            raise HubTaskContractError("invalid_hub_artifact_references")
        if not isinstance(events, list):
            raise HubTaskContractError("invalid_hub_event_references")
        receipt = cls(
            schema=str(raw.get("schema")),
            hub_task_id=str(raw.get("hub_task_id") or ""),
            operation_id=str(raw.get("operation_id") or ""),
            status=str(raw.get("status") or ""),
            authorization_state=str(raw.get("authorization_state") or ""),
            ledger_state=str(raw.get("ledger_state") or ""),
            artifact_refs=tuple(dict(item) for item in artifacts),
            canonical_event_refs=tuple(str(item) for item in events if str(item).strip()),
            checkpoint_ref=str(raw.get("checkpoint_ref") or ""),
            reason_code=str(raw.get("reason_code") or ""),
        )
        receipt.validate()
        return receipt

    def validate(self) -> None:
        if not self.hub_task_id or len(self.hub_task_id) > 256:
            raise HubTaskContractError("invalid_hub_task_id")
        if not self.operation_id or len(self.operation_id) > 256:
            raise HubTaskContractError("invalid_operation_id")
        if self.status not in HUB_TASK_STATUSES:
            raise HubTaskContractError("invalid_hub_task_status")
        if self.authorization_state != "valid":
            raise HubTaskContractError("hub_authorization_not_revalidated")
        if self.ledger_state not in HUB_LEDGER_STATES:
            raise HubTaskContractError("hub_ledger_decision_missing")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "hub_task_id": self.hub_task_id,
            "operation_id": self.operation_id,
            "status": self.status,
            "authorization_state": self.authorization_state,
            "ledger_state": self.ledger_state,
            "artifact_refs": [dict(item) for item in self.artifact_refs],
            "canonical_event_refs": list(self.canonical_event_refs),
            "checkpoint_ref": self.checkpoint_ref,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class RetryBudgetReceipt:
    """Payload-free acknowledgement from the Hub-owned combined retry budget."""

    retry_id: str
    category: str
    used: int
    maximum: int
    remaining: int
    schema: str = RETRY_BUDGET_RECEIPT_SCHEMA

    @classmethod
    def from_mapping(cls, raw: object) -> "RetryBudgetReceipt":
        if not isinstance(raw, Mapping) or str(raw.get("schema") or "") != RETRY_BUDGET_RECEIPT_SCHEMA:
            raise HubTaskContractError("invalid_retry_budget_response")
        try:
            receipt = cls(
                schema=str(raw.get("schema")),
                retry_id=str(raw.get("retry_id") or ""),
                category=str(raw.get("category") or ""),
                used=int(raw.get("used")),
                maximum=int(raw.get("maximum")),
                remaining=int(raw.get("remaining")),
            )
        except (TypeError, ValueError) as exc:
            raise HubTaskContractError("invalid_retry_budget_response") from exc
        receipt.validate()
        return receipt

    def validate(self) -> None:
        if not self.retry_id or len(self.retry_id) > 256 or "\x00" in self.retry_id:
            raise HubTaskContractError("invalid_retry_id")
        if self.category not in RETRY_CATEGORIES:
            raise HubTaskContractError("invalid_retry_category")
        if self.maximum < 0 or self.used < 0 or self.used > self.maximum:
            raise HubTaskContractError("invalid_retry_budget_values")
        if self.remaining != self.maximum - self.used:
            raise HubTaskContractError("invalid_retry_budget_remaining")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "retry_id": self.retry_id,
            "category": self.category,
            "used": self.used,
            "maximum": self.maximum,
            "remaining": self.remaining,
        }


__all__ = [
    "HUB_LEDGER_STATES",
    "HUB_TASK_COMMAND_SCHEMA",
    "HUB_TASK_RECEIPT_SCHEMA",
    "HUB_TASK_STATUSES",
    "RETRY_BUDGET_RECEIPT_SCHEMA",
    "RETRY_CATEGORIES",
    "HubTaskContractError",
    "HubTaskReceipt",
    "RetryBudgetReceipt",
]
