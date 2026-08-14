"""Public-key verification boundary for Hub-issued Temporal commands.

Cryptography runs as a side-effect-free Local Activity, outside the deterministic
workflow sandbox.  Deterministic denials return a typed decision; unexpected
infrastructure failures propagate so the Hub cannot mistake them for rejection.
"""

from __future__ import annotations

from typing import Any, Protocol

from temporalio import activity

from ananta_contracts.runtime_authorization_crypto import (
    Ed25519VerificationKeyRing,
    RuntimeAuthorizationCryptoError,
)
from ananta_contracts.temporal_workflow import (
    COMMAND_AUTHORITY_ACTIVITY,
    COMMAND_AUTHORITY_RESULT_SCHEMA,
    COMMAND_SCHEMA,
    LEGACY_COMMAND_SCHEMA,
    TemporalContractError,
    WorkflowCommand,
    WorkflowCommandAuthorityResult,
)


class WorkflowCommandAuthorityVerifierPort(Protocol):
    def verify(self, command: WorkflowCommand) -> WorkflowCommandAuthorityResult: ...


class PublicKeyWorkflowCommandAuthorityVerifier:
    """Verify commands with an Ed25519 public-key ring and no signing port."""

    def __init__(self, verification_key_ring: Ed25519VerificationKeyRing) -> None:
        if not isinstance(verification_key_ring, Ed25519VerificationKeyRing):
            raise TypeError("temporal_command_ed25519_verification_keyring_required")
        self._key_ring = verification_key_ring

    def verify(self, command: WorkflowCommand) -> WorkflowCommandAuthorityResult:
        if command.schema == COMMAND_SCHEMA and command.signature_algorithm != self._key_ring.signature_algorithm:
            return _rejected(command, "unsupported_command_signature_algorithm")
        if command.schema not in {LEGACY_COMMAND_SCHEMA, COMMAND_SCHEMA}:
            return _rejected(command, "unsupported_command_schema")
        try:
            self._key_ring.verify(
                namespace=command.schema,
                payload=command.signing_payload(),
                key_id=command.key_id,
                signature=command.signature,
                contract_id=command.command_id,
            )
        except RuntimeAuthorizationCryptoError as exc:
            return _rejected(command, exc.reason_code)
        return WorkflowCommandAuthorityResult(
            accepted=True,
            command_id=command.command_id,
            payload_digest=command.computed_payload_digest(),
            signature_algorithm=self._key_ring.signature_algorithm,
            key_id=command.key_id,
        )


class FailClosedWorkflowCommandAuthorityVerifier:
    def verify(self, command: WorkflowCommand) -> WorkflowCommandAuthorityResult:
        return _rejected(command, "temporal_command_verification_keyring_required")


class WorkflowCommandAuthorityActivity:
    """Local Activity adapter returning one stable authorization decision."""

    def __init__(self, verifier: WorkflowCommandAuthorityVerifierPort | None = None) -> None:
        self._verifier = verifier or FailClosedWorkflowCommandAuthorityVerifier()

    @activity.defn(name=COMMAND_AUTHORITY_ACTIVITY)
    async def verify(self, raw_command: dict[str, Any]) -> dict[str, Any]:
        try:
            command = WorkflowCommand.from_mapping(raw_command)
        except TemporalContractError as exc:
            return WorkflowCommandAuthorityResult(
                accepted=False,
                reason_code=exc.reason_code,
            ).to_dict()
        return self._verifier.verify(command).to_dict()


def _rejected(command: WorkflowCommand, reason_code: str) -> WorkflowCommandAuthorityResult:
    return WorkflowCommandAuthorityResult(
        accepted=False,
        command_id=command.command_id,
        payload_digest=command.computed_payload_digest(),
        signature_algorithm=command.signature_algorithm or "ed25519",
        key_id=command.key_id,
        reason_code=str(reason_code or "temporal_command_authority_verification_failed"),
    )


__all__ = [
    "COMMAND_AUTHORITY_ACTIVITY",
    "COMMAND_AUTHORITY_RESULT_SCHEMA",
    "FailClosedWorkflowCommandAuthorityVerifier",
    "PublicKeyWorkflowCommandAuthorityVerifier",
    "WorkflowCommandAuthorityActivity",
    "WorkflowCommandAuthorityResult",
    "WorkflowCommandAuthorityVerifierPort",
]
