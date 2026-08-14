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

    def verify_persisted(
        self,
        *,
        tenant_id: str,
        run_id: str,
        command: dict[str, Any],
    ) -> SignedWorkflowCommand: ...

    def verify_for_staging(
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
        signed = self._signed(command)
        self._verify(signed, tenant_id=tenant_id, run_id=run_id, consume=True)
        return signed

    def verify_persisted(
        self,
        *,
        tenant_id: str,
        run_id: str,
        command: dict[str, Any],
    ) -> SignedWorkflowCommand:
        """Reverify a staged Hub intent without consuming its nonce twice."""

        signed = self._signed(command)
        self._verify(signed, tenant_id=tenant_id, run_id=run_id, consume=False)
        return signed

    def verify_for_staging(
        self,
        *,
        tenant_id: str,
        run_id: str,
        command: dict[str, Any],
    ) -> SignedWorkflowCommand:
        """Verify current admission without consuming the nonce.

        The production intent store consumes it in the same transaction that
        stages the dispatch outbox and binding claim.
        """

        signed = self._signed(command)
        self._verify_admission(signed, tenant_id=tenant_id, run_id=run_id)
        return signed

    @staticmethod
    def _signed(command: dict[str, Any]) -> SignedWorkflowCommand:
        if str(command.get("schema") or "") != "ananta.durable_run_signal.v1":
            raise ValueError("durable_run_signal_schema_unsupported")
        raw = command.get("command")
        if not isinstance(raw, dict):
            raise ValueError("durable_run_signed_command_required")
        return SignedWorkflowCommand.from_mapping(raw)

    def _verify(
        self,
        signed: SignedWorkflowCommand,
        *,
        tenant_id: str,
        run_id: str,
        consume: bool,
    ) -> None:
        verifier = self._verifier.verify_once if consume else self._verifier.verify_persisted
        verifier(
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

    def _verify_admission(
        self,
        signed: SignedWorkflowCommand,
        *,
        tenant_id: str,
        run_id: str,
    ) -> None:
        self._verifier.verify(
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


__all__ = ["HubSignedWorkflowCommandVerifier", "HubVerifiedDurableCommandPort"]
