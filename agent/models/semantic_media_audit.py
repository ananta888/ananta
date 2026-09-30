"""Dependency-free contracts of the content-free semantic-media audit trail.

The audit event value type, its error type, bounds and the idempotency
comparison are shared by the Hub audit service and the persistence adapters
that stage events inside their authority transactions. Keeping them in the
model layer lets ``agent.repositories`` depend on the contract instead of on
``agent.services`` (DIP). ``agent.services.semantic_media_audit_service``
re-exports every name unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from agent.models.semantic_media_content_policy import assert_content_free

MAX_RETENTION_MS = 30 * 24 * 60 * 60 * 1000
MIN_RETENTION_MS = 60 * 60 * 1000
MAX_PAGE_SIZE = 100
MAX_SCOPE_EVENTS = 10_000
REFERENCE_FIELDS = ("contract_ref", "lease_ref", "job_ref")
AUDIT_EVENT_TYPES = frozenset(
    {
        "semantic_budget",
        "semantic_admission",
        "semantic_consent",
        "semantic_contract",
        "semantic_fallback",
        "semantic_job",
        "semantic_lease",
        "semantic_recovery",
        "semantic_rekey",
        "semantic_relay",
        "speech_adapter",
        "speech_dataset",
        "speech_evidence",
        "speech_training",
    }
)


class SemanticMediaAuditError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 422) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class SemanticMediaAuditEvent:
    event_id: str
    idempotency_digest: str
    tenant_digest: str
    scope_digest: str
    event_type: str
    transition: str
    reason_code: str
    epoch: int
    contract_ref: str | None
    lease_ref: str | None
    job_ref: str | None
    created_at_ms: int
    expires_at_ms: int

    def public(self) -> dict[str, object]:
        result = asdict(self)
        result.pop("idempotency_digest", None)
        assert_content_free(result)
        return result


def same_idempotent_audit_request(
    first: SemanticMediaAuditEvent,
    second: SemanticMediaAuditEvent,
) -> bool:
    """Compare the command binding, excluding first-write timestamps and ID."""

    return all(
        getattr(first, field) == getattr(second, field)
        for field in (
            "idempotency_digest",
            "tenant_digest",
            "scope_digest",
            "event_type",
            "transition",
            "reason_code",
            "epoch",
            "contract_ref",
            "lease_ref",
            "job_ref",
        )
    )


__all__ = [
    "AUDIT_EVENT_TYPES",
    "MAX_PAGE_SIZE",
    "MAX_RETENTION_MS",
    "MAX_SCOPE_EVENTS",
    "MIN_RETENTION_MS",
    "REFERENCE_FIELDS",
    "SemanticMediaAuditError",
    "SemanticMediaAuditEvent",
    "same_idempotent_audit_request",
]
