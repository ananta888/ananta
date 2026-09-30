"""Idempotent operation receipts and audit outbox staging for Organization definition mutations."""

from __future__ import annotations

from agent.db_models.organizations import (
    OrganizationAuditOutboxDB,
)
from agent.services.organization_definition_errors import OrganizationDefinitionMutationError


def replay_definition_operation(
    uow,
    *,
    tenant_id,
    project_id,
    operation_kind,
    idempotency_key,
    request_digest,
):
    existing = uow.operations.get_by_idempotency_key(
        tenant_id,
        project_id,
        operation_kind,
        idempotency_key,
        for_update=True,
    )
    if existing is None:
        return None
    if existing.request_digest != request_digest:
        raise OrganizationDefinitionMutationError("organization_idempotency_key_conflict")
    if existing.status != "applied":
        raise OrganizationDefinitionMutationError("organization_definition_mutation_in_progress")
    return {**dict(existing.result_json or {}), "replayed": True}


def finish_definition_operation(
    uow,
    *,
    operation,
    event_kind,
    event_key,
    result,
    principal_id,
    now,
) -> None:
    uow.audit_outbox.add(
        OrganizationAuditOutboxDB(
            tenant_id=operation.tenant_id,
            project_id=operation.project_id,
            organization_id=None,
            event_key=event_key,
            event_kind=event_kind,
            payload_json={**result, "principal_id": principal_id},
        )
    )
    operation.status = "applied"
    operation.result_ref = str(result.get("definition_key") or "")
    operation.result_json = result
    operation.applied_at = now
    uow.operations.add(operation)


__all__ = [
    "finish_definition_operation",
    "replay_definition_operation",
]
