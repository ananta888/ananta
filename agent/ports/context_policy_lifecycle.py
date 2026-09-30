"""Persistence, lint, preview and audit ports of the Context Policy lifecycle."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from agent.models.context_policy_lifecycle import (
    ContextPolicyDiagnostic,
    ContextPolicyPreview,
    ContextPolicyVersion,
)
from ananta_contracts.source_control import GrantOperation, GrantTransformation


class ContextPolicyLifecycleRepositoryPort(Protocol):
    def latest(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
    ) -> ContextPolicyVersion | None: ...

    def get_version(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        version: int,
    ) -> ContextPolicyVersion | None: ...

    def list_versions(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        cursor: str | None,
        limit: int,
    ) -> tuple[Sequence[ContextPolicyVersion], str | None]: ...

    def active(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
    ) -> ContextPolicyVersion | None: ...

    def get_mutation_result(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        operation: str,
        idempotency_key: str,
        request_digest: str,
    ) -> ContextPolicyVersion | None: ...

    def append_draft(
        self,
        *,
        version: ContextPolicyVersion,
        expected_latest_version: int | None,
        operation: str | None = None,
        idempotency_key: str | None = None,
        request_digest: str | None = None,
    ) -> ContextPolicyVersion: ...

    def transition(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        version: int,
        expected_etag: str,
        target_state: str,
        actor_id: str,
        operation: str | None = None,
        idempotency_key: str | None = None,
        request_digest: str | None = None,
    ) -> ContextPolicyVersion: ...


class ContextPolicyLintPort(Protocol):
    def lint(
        self,
        *,
        document: Mapping[str, Any],
    ) -> Sequence[ContextPolicyDiagnostic]: ...


class ContextPolicyPreviewPort(Protocol):
    def preview(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_document: Mapping[str, Any],
        source_revision_id: str,
        destination_id: str,
        operation: GrantOperation,
        transformation: GrantTransformation,
    ) -> ContextPolicyPreview: ...


class ContextPolicyAuditPort(Protocol):
    def record(
        self,
        *,
        operation: str,
        actor_id: str,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        version: int,
        policy_digest: str,
        reason_code: str,
    ) -> None: ...


__all__ = [
    "ContextPolicyAuditPort",
    "ContextPolicyLifecycleRepositoryPort",
    "ContextPolicyLintPort",
    "ContextPolicyPreviewPort",
]
