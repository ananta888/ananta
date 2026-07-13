"""Hub-side verification boundary for durable runtime control commands."""

from __future__ import annotations

from typing import Any, Protocol

from agent.services.workflow_runtime.commands import (
    SignedWorkflowCommand,
    WorkflowCommandVerifier,
)


class HubVerifiedDurableCommandPort(Protocol):
    """Verify a signed, revision-bound Hub command before transport."""

    def verify(
        self,
        *,
        tenant_id: str,
        run_id: str,
        command: dict[str, Any],
    ) -> SignedWorkflowCommand: ...


class HubSignedWorkflowCommandVerifier:
    """Cryptographically verify and consume one Hub-issued command nonce."""

    def __init__(self, verifier: WorkflowCommandVerifier) -> None:
        self._verifier = verifier

    def verify(
        self,
        *,
        tenant_id: str,
        run_id: str,
        command: dict[str, Any],
    ) -> SignedWorkflowCommand:
        raw = command.get("command")
        if not isinstance(raw, dict):
            raise ValueError("durable_run_signed_command_required")
        signed = SignedWorkflowCommand.from_mapping(raw)
        self._verifier.verify_once(
            signed,
            tenant_id=tenant_id,
            workflow_id=run_id,
            run_id=signed.run_id,
            step_id=signed.step_id,
            checkpoint_id=signed.checkpoint_id,
            expected_revision=signed.expected_revision,
            plan_hash=signed.plan_hash,
            policy_version=signed.policy_version,
        )
        return signed


__all__ = ["HubSignedWorkflowCommandVerifier", "HubVerifiedDurableCommandPort"]
