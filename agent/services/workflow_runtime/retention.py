"""Audit-safe retention rules for canonical workflow events.

Canonical events are the rebuild source of truth, so deleting individual rows
would silently destroy replay and projection integrity.  The production policy
therefore retains the already-redacted canonical envelope append-only and puts
short-lived raw runtime diagnostics behind references.  This module produces a
payload-free hash-chain attestation that operators can retain after an external
raw-history store has expired its content.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable

from agent.services.workflow_runtime._serialization import canonical_json, redact_json
from agent.services.workflow_runtime.errors import ContractValidationError
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent

RETENTION_POLICY_SCHEMA = "ananta.workflow_event_retention_policy.v1"
RETENTION_ATTESTATION_SCHEMA = "ananta.workflow_event_retention_attestation.v1"


@dataclass(frozen=True)
class WorkflowEventRetentionPolicy:
    """Versioned policy that never permits partial canonical-history purges."""

    policy_version: str
    raw_diagnostic_ttl_seconds: int = 604_800
    mode: str = "append_only_redacted"
    redaction_version: str = "ananta-redaction-v1"
    schema: str = RETENTION_POLICY_SCHEMA

    def assert_valid(self) -> None:
        if self.schema != RETENTION_POLICY_SCHEMA:
            raise ContractValidationError("retention_policy_schema_unsupported")
        if not self.policy_version or len(self.policy_version) > 256:
            raise ContractValidationError("retention_policy_version_invalid")
        if self.mode != "append_only_redacted":
            raise ContractValidationError("canonical_event_purge_forbidden")
        if not 3_600 <= self.raw_diagnostic_ttl_seconds <= 31_536_000:
            raise ContractValidationError("raw_diagnostic_retention_invalid")
        if not self.redaction_version or len(self.redaction_version) > 128:
            raise ContractValidationError("retention_redaction_version_invalid")


@dataclass(frozen=True)
class WorkflowEventRetentionAttestation:
    tenant_id: str
    run_id: str
    policy_version: str
    redaction_version: str
    event_count: int
    first_sequence: int
    last_sequence: int
    content_chain_hash: str
    raw_diagnostics_expire_before: float
    event_refs: tuple[tuple[int, str, str], ...]
    schema: str = RETENTION_ATTESTATION_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "policy_version": self.policy_version,
            "redaction_version": self.redaction_version,
            "event_count": self.event_count,
            "first_sequence": self.first_sequence,
            "last_sequence": self.last_sequence,
            "content_chain_hash": self.content_chain_hash,
            "raw_diagnostics_expire_before": self.raw_diagnostics_expire_before,
            "event_refs": [
                {"sequence": sequence, "event_id": event_id, "content_hash": content_hash}
                for sequence, event_id, content_hash in self.event_refs
            ],
        }


class WorkflowEventRetentionService:
    """Build and verify payload-free integrity attestations for one run."""

    def __init__(self, policy: WorkflowEventRetentionPolicy) -> None:
        policy.assert_valid()
        self._policy = policy

    def attest(
        self,
        *,
        tenant_id: str,
        run_id: str,
        events: Iterable[CanonicalWorkflowEvent],
        evaluated_at: float,
    ) -> WorkflowEventRetentionAttestation:
        ordered = tuple(sorted(events, key=lambda item: item.sequence))
        if not tenant_id or not run_id or evaluated_at <= 0:
            raise ContractValidationError("retention_binding_invalid")
        previous_sequence = 0
        chain = hashlib.sha256(
            canonical_json(
                {
                    "schema": RETENTION_ATTESTATION_SCHEMA,
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "policy_version": self._policy.policy_version,
                    "redaction_version": self._policy.redaction_version,
                }
            ).encode("utf-8")
        ).hexdigest()
        refs: list[tuple[int, str, str]] = []
        for event in ordered:
            event.assert_valid()
            if event.tenant_id != tenant_id or event.run_id != run_id:
                raise ContractValidationError("retention_event_binding_mismatch")
            if event.sequence != previous_sequence + 1:
                raise ContractValidationError("retention_event_sequence_gap")
            if dict(redact_json(event.payload)) != event.payload:
                raise ContractValidationError("retention_unredacted_event_denied")
            chain = hashlib.sha256(
                canonical_json(
                    {
                        "previous": chain,
                        "sequence": event.sequence,
                        "event_id": event.event_id,
                        "content_hash": event.content_hash,
                    }
                ).encode("utf-8")
            ).hexdigest()
            refs.append((event.sequence, event.event_id, event.content_hash))
            previous_sequence = event.sequence
        return WorkflowEventRetentionAttestation(
            tenant_id=tenant_id,
            run_id=run_id,
            policy_version=self._policy.policy_version,
            redaction_version=self._policy.redaction_version,
            event_count=len(ordered),
            first_sequence=ordered[0].sequence if ordered else 0,
            last_sequence=ordered[-1].sequence if ordered else 0,
            content_chain_hash=chain,
            raw_diagnostics_expire_before=(
                float(evaluated_at) - self._policy.raw_diagnostic_ttl_seconds
            ),
            event_refs=tuple(refs),
        )

    def verify(
        self,
        attestation: WorkflowEventRetentionAttestation,
        *,
        events: Iterable[CanonicalWorkflowEvent],
        evaluated_at: float,
    ) -> None:
        rebuilt = self.attest(
            tenant_id=attestation.tenant_id,
            run_id=attestation.run_id,
            events=events,
            evaluated_at=evaluated_at,
        )
        if canonical_json(rebuilt.to_dict()) != canonical_json(attestation.to_dict()):
            raise ContractValidationError("retention_attestation_mismatch")


__all__ = [
    "RETENTION_ATTESTATION_SCHEMA",
    "RETENTION_POLICY_SCHEMA",
    "WorkflowEventRetentionAttestation",
    "WorkflowEventRetentionPolicy",
    "WorkflowEventRetentionService",
]
