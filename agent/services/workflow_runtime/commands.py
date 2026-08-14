"""Signed, replay-safe commands for workflow control and approvals.

Commands are hub contracts, not runtime-specific signals.  A runtime may only
apply a command after its signature and all bindings have been checked at the
hub boundary.  The payload is deliberately small and rejects embedded secrets;
large edits belong in an artifact referenced by the command.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from agent.services.workflow_runtime._serialization import canonical_json, contains_sensitive_keys, redact_json
from agent.services.workflow_runtime.errors import ContractValidationError, SignatureValidationError
from agent.services.workflow_runtime.security import (
    ReplayNonceStore,
    SignatureSigningKeyRingPort,
    SignatureVerificationKeyRingPort,
)

WORKFLOW_COMMAND_SCHEMA = "ananta.workflow_command.v2"
WORKFLOW_COMMAND_TYPES = frozenset(
    {
        "approve",
        "reject",
        "edit",
        "request_changes",
        "pause",
        "resume",
        "cancel",
        "retry",
        "parameter_update",
    }
)


@dataclass(frozen=True)
class SignedWorkflowCommand:
    command_id: str
    command_type: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    checkpoint_id: str
    expected_revision: int
    plan_hash: str
    policy_version: str
    actor_id: str
    actor_roles: tuple[str, ...]
    payload: dict[str, Any]
    issued_at: float
    expires_at: float
    nonce: str
    key_id: str
    signature: str
    schema: str = WORKFLOW_COMMAND_SCHEMA

    @classmethod
    def issue(
        cls,
        *,
        key_ring: SignatureSigningKeyRingPort,
        command_type: str,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        checkpoint_id: str,
        expected_revision: int,
        plan_hash: str,
        policy_version: str,
        actor_id: str,
        actor_roles: tuple[str, ...] | list[str] = (),
        payload: dict[str, Any] | None = None,
        ttl_seconds: float = 300.0,
        now: float | None = None,
        command_id: str | None = None,
        nonce: str | None = None,
    ) -> "SignedWorkflowCommand":
        timestamp = float(now if now is not None else time.time())
        if ttl_seconds <= 0:
            raise ContractValidationError("workflow_command_ttl_invalid")
        command = cls(
            command_id=str(command_id or f"wcmd-{uuid.uuid4().hex}"),
            command_type=str(command_type).strip(),
            tenant_id=str(tenant_id).strip(),
            workflow_id=str(workflow_id).strip(),
            run_id=str(run_id).strip(),
            step_id=str(step_id).strip(),
            checkpoint_id=str(checkpoint_id).strip(),
            expected_revision=int(expected_revision),
            plan_hash=str(plan_hash).strip(),
            policy_version=str(policy_version).strip(),
            actor_id=str(actor_id).strip(),
            actor_roles=_clean_tuple(actor_roles),
            payload=dict(payload or {}),
            issued_at=timestamp,
            expires_at=timestamp + float(ttl_seconds),
            nonce=str(nonce or uuid.uuid4().hex),
            key_id=key_ring.active_key_id,
            signature="",
        )
        command._assert_structure()
        key_id, signature = key_ring.sign(
            namespace=WORKFLOW_COMMAND_SCHEMA,
            payload=command._signing_payload(),
            key_id=command.key_id,
        )
        return replace(command, key_id=key_id, signature=signature)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "SignedWorkflowCommand":
        command = cls(
            command_id=str(raw.get("command_id") or ""),
            command_type=str(raw.get("command_type") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            step_id=str(raw.get("step_id") or ""),
            checkpoint_id=str(raw.get("checkpoint_id") or ""),
            expected_revision=int(raw.get("expected_revision", -1)),
            plan_hash=str(raw.get("plan_hash") or ""),
            policy_version=str(raw.get("policy_version") or ""),
            actor_id=str(raw.get("actor_id") or ""),
            actor_roles=_clean_tuple(raw.get("actor_roles") or ()),
            payload=dict(raw.get("payload") or {}),
            issued_at=float(raw.get("issued_at") or 0),
            expires_at=float(raw.get("expires_at") or 0),
            nonce=str(raw.get("nonce") or ""),
            key_id=str(raw.get("key_id") or ""),
            signature=str(raw.get("signature") or ""),
            schema=str(raw.get("schema") or WORKFLOW_COMMAND_SCHEMA),
        )
        command._assert_structure()
        return command

    def verify(
        self,
        *,
        key_ring: SignatureVerificationKeyRingPort,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        checkpoint_id: str,
        expected_revision: int,
        plan_hash: str,
        policy_version: str,
        now: float | None = None,
    ) -> None:
        self._assert_structure()
        expected = {
            "tenant_id": tenant_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "step_id": step_id,
            "checkpoint_id": checkpoint_id,
            "expected_revision": expected_revision,
            "plan_hash": plan_hash,
            "policy_version": policy_version,
        }
        for field_name, expected_value in expected.items():
            if str(getattr(self, field_name)) != str(expected_value):
                raise SignatureValidationError(f"workflow_command_{field_name}_mismatch")
        timestamp = float(now if now is not None else time.time())
        if timestamp < self.issued_at:
            raise SignatureValidationError("workflow_command_not_yet_valid")
        if timestamp >= self.expires_at:
            raise SignatureValidationError("workflow_command_expired")
        key_ring.verify(
            namespace=WORKFLOW_COMMAND_SCHEMA,
            payload=self._signing_payload(),
            key_id=self.key_id,
            signature=self.signature,
            contract_id=self.command_id,
        )

    def _assert_structure(self) -> None:
        if self.schema != WORKFLOW_COMMAND_SCHEMA:
            raise ContractValidationError("workflow_command_schema_unsupported")
        required = (
            self.command_id,
            self.command_type,
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.step_id,
            self.checkpoint_id,
            self.plan_hash,
            self.policy_version,
            self.actor_id,
            self.nonce,
            self.key_id,
        )
        if any(not value for value in required):
            raise ContractValidationError("workflow_command_binding_required")
        if self.command_type not in WORKFLOW_COMMAND_TYPES:
            raise ContractValidationError("workflow_command_type_unsupported")
        if isinstance(self.expected_revision, bool) or self.expected_revision < 0:
            raise ContractValidationError("workflow_command_revision_invalid")
        if self.expires_at <= self.issued_at:
            raise ContractValidationError("workflow_command_expiry_invalid")
        if contains_sensitive_keys(self.payload):
            raise ContractValidationError("workflow_command_embedded_secret_denied")
        if len(canonical_json(self.payload).encode("utf-8")) > 65_536:
            raise ContractValidationError("workflow_command_payload_too_large")
        if self.command_type in {"edit", "request_changes"}:
            if not (self.payload.get("plan_ref") or self.payload.get("replacement_plan")):
                raise ContractValidationError("workflow_command_plan_edit_required")
            replacement_hash = str(self.payload.get("replacement_plan_hash") or "")
            if len(replacement_hash) != 64 or any(
                character not in "0123456789abcdefABCDEF" for character in replacement_hash
            ):
                raise ContractValidationError("workflow_command_replacement_plan_hash_required")

    def _signing_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("signature", None)
        return payload

    def to_dict(self, *, redacted: bool = False) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "command_id": self.command_id,
            "command_type": self.command_type,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "checkpoint_id": self.checkpoint_id,
            "expected_revision": self.expected_revision,
            "plan_hash": self.plan_hash,
            "policy_version": self.policy_version,
            "actor_id": self.actor_id,
            "actor_roles": list(self.actor_roles),
            "payload": dict(redact_json(self.payload)),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "key_id": self.key_id,
            "signature": self.signature,
        }
        if redacted:
            payload["nonce"] = "[REDACTED]"
            payload["signature"] = "[REDACTED]"
        return payload


class WorkflowCommandVerifier:
    """Verify a command once; duplicate submissions fail closed."""

    def __init__(self, key_ring: SignatureVerificationKeyRingPort, replay_store: ReplayNonceStore):
        self._key_ring = key_ring
        self._replay_store = replay_store

    def verify(self, command: SignedWorkflowCommand, **bindings: Any) -> None:
        """Verify immutable signature/bindings without consuming replay state.

        This is reserved for Hub outbox replay of an intent whose nonce was
        consumed before it was staged.  Public submission paths use
        :meth:`verify_once`.
        """

        command.verify(key_ring=self._key_ring, **bindings)

    def verify_persisted(self, command: SignedWorkflowCommand, **bindings: Any) -> None:
        """Verify a previously admitted intent after its submission TTL.

        Expiry is an admission fence, not a deadline for the durable Hub
        outbox.  Replays still verify the original signature and every binding.
        """

        persisted_bindings = dict(bindings)
        persisted_bindings.pop("now", None)
        command.verify(
            key_ring=self._key_ring,
            now=command.issued_at,
            **persisted_bindings,
        )

    def verify_once(self, command: SignedWorkflowCommand, **bindings: Any) -> None:
        self.verify(command, **bindings)
        if not self._replay_store.consume(
            tenant_id=command.tenant_id,
            nonce=command.nonce,
            expires_at=command.expires_at,
        ):
            raise SignatureValidationError("workflow_command_replay_detected")


class WorkflowCommandIssuer:
    """Hub-only signer for canonical workflow control decisions."""

    def __init__(
        self,
        key_ring: SignatureSigningKeyRingPort,
        *,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("workflow_command_ttl_invalid")
        self._key_ring = key_ring
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock

    def issue(
        self,
        *,
        command_id: str,
        command_type: str,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        checkpoint_id: str,
        expected_revision: int,
        plan_hash: str,
        policy_version: str,
        actor_id: str,
        actor_roles: tuple[str, ...],
        payload: dict[str, Any],
    ) -> SignedWorkflowCommand:
        return SignedWorkflowCommand.issue(
            key_ring=self._key_ring,
            command_id=command_id,
            command_type=command_type,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            checkpoint_id=checkpoint_id,
            expected_revision=expected_revision,
            plan_hash=plan_hash,
            policy_version=policy_version,
            actor_id=actor_id,
            actor_roles=actor_roles,
            payload=payload,
            ttl_seconds=self._ttl_seconds,
            now=float(self._clock()),
        )


def _clean_tuple(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))
