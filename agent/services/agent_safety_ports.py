"""Narrow infrastructure ports for agent safety containment."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from ananta_contracts.agent_safety import SafetyAction, utc_now


@dataclass(frozen=True, slots=True)
class SafetyControlReceipt:
    operation_id: str
    run_id: str
    sandbox_id: str
    action: SafetyAction
    enforced: bool
    reason_code: str
    observed_at: str

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["action"] = self.action.value
        return value


class SandboxSafetyControlPort(Protocol):
    def apply(
        self, *, operation_id: str, run_id: str, sandbox_id: str, action: SafetyAction, reason: str
    ) -> SafetyControlReceipt: ...


class EgressFencePort(Protocol):
    def deny(self, *, operation_id: str, run_id: str, sandbox_id: str) -> SafetyControlReceipt: ...


class CredentialLeaseRevocationPort(Protocol):
    def revoke(self, *, operation_id: str, run_id: str) -> SafetyControlReceipt: ...


class UnavailableSafetyControl:
    """Fail-closed default; it never reports an unsupported control as enforced."""

    def apply(
        self, *, operation_id: str, run_id: str, sandbox_id: str, action: SafetyAction, reason: str
    ) -> SafetyControlReceipt:
        del reason
        return SafetyControlReceipt(
            operation_id, run_id, sandbox_id, action, False, "sandbox_safety_adapter_unavailable", utc_now()
        )


class UnavailableEgressFence:
    def deny(self, *, operation_id: str, run_id: str, sandbox_id: str) -> SafetyControlReceipt:
        return SafetyControlReceipt(
            operation_id, run_id, sandbox_id, SafetyAction.ISOLATE, False, "egress_fence_adapter_unavailable", utc_now()
        )


class UnavailableCredentialRevocation:
    def revoke(self, *, operation_id: str, run_id: str) -> SafetyControlReceipt:
        return SafetyControlReceipt(
            operation_id,
            run_id,
            "run-credentials",
            SafetyAction.ISOLATE,
            False,
            "credential_revocation_adapter_unavailable",
            utc_now(),
        )


class RecordingSafetyAdapter(UnavailableSafetyControl, UnavailableEgressFence, UnavailableCredentialRevocation):
    """Deterministic enforcing adapter for isolated automated tests."""

    def __init__(self) -> None:
        self.receipts: list[SafetyControlReceipt] = []

    def apply(
        self, *, operation_id: str, run_id: str, sandbox_id: str, action: SafetyAction, reason: str
    ) -> SafetyControlReceipt:
        del reason
        receipt = SafetyControlReceipt(
            operation_id, run_id, sandbox_id, action, True, "sandbox_control_enforced", utc_now()
        )
        self.receipts.append(receipt)
        return receipt

    def deny(self, *, operation_id: str, run_id: str, sandbox_id: str) -> SafetyControlReceipt:
        receipt = SafetyControlReceipt(
            operation_id, run_id, sandbox_id, SafetyAction.ISOLATE, True, "egress_fence_enforced", utc_now()
        )
        self.receipts.append(receipt)
        return receipt

    def revoke(self, *, operation_id: str, run_id: str) -> SafetyControlReceipt:
        receipt = SafetyControlReceipt(
            operation_id, run_id, "run-credentials", SafetyAction.ISOLATE, True, "credential_leases_revoked", utc_now()
        )
        self.receipts.append(receipt)
        return receipt


__all__ = [
    "CredentialLeaseRevocationPort",
    "EgressFencePort",
    "RecordingSafetyAdapter",
    "SafetyControlReceipt",
    "SandboxSafetyControlPort",
    "UnavailableCredentialRevocation",
    "UnavailableEgressFence",
    "UnavailableSafetyControl",
]
