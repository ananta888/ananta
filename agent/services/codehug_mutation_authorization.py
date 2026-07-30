"""Server-side CodeHug mutation authorization and execution boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Protocol

from agent.services.effective_source_access_service import (
    EffectiveSourceAccessService,
)
from agent.services.source_access_enforcement import (
    DelegatedSourceEnforcementManifest,
    SourceAccessEnforcementService,
    SourceAccessRequest,
)
from ananta_contracts.source_control import GrantOperation, GrantTransformation


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CodeHugMutationAuthorizationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class CodeHugToolAuthorization:
    tool_id: str
    operation: GrantOperation
    mutating: bool
    enabled: bool


class CodeHugToolCatalogPort(Protocol):
    def resolve(self, *, tool_id: str) -> CodeHugToolAuthorization | None: ...


@dataclass(frozen=True)
class CodeHugMutationCommand:
    tenant_id: str
    project_id: str
    actor_id: str
    source_revision_id: str
    destination_id: str
    destination_digest: str
    expected_revision_digest: str
    expected_policy_digest: str
    tool_id: str
    transformation: GrantTransformation
    purpose: str
    content_manifest_id: str
    content_manifest_digest: str
    assignment_id: str
    lease_id: str
    payload_reference_id: str
    source_access_grant_id: str
    source_access_grant_digest: str

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "project_id",
            "actor_id",
            "source_revision_id",
            "destination_id",
            "tool_id",
            "purpose",
            "content_manifest_id",
            "assignment_id",
            "lease_id",
            "payload_reference_id",
            "source_access_grant_id",
        ):
            if not _OPAQUE_ID.fullmatch(str(getattr(self, name) or "")):
                raise CodeHugMutationAuthorizationError(f"{name}_invalid")
        for name in (
            "destination_digest",
            "expected_revision_digest",
            "expected_policy_digest",
            "content_manifest_digest",
            "source_access_grant_digest",
        ):
            if not _SHA256.fullmatch(str(getattr(self, name) or "")):
                raise CodeHugMutationAuthorizationError(f"{name}_invalid")


class CodeHugMutationExecutorPort(Protocol):
    def execute(
        self,
        *,
        tool_id: str,
        payload_reference_id: str,
        enforcement_manifest: DelegatedSourceEnforcementManifest,
    ) -> Mapping[str, object]: ...


class CodeHugSecurityAuditPort(Protocol):
    def record(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
        destination_id: str,
        tool_id: str,
        operation: str,
        transformation: str,
        decision: str,
        reason_code: str,
        binding_digest: str | None,
    ) -> None: ...


class CodeHugMutationAuthorizationService:
    def __init__(
        self,
        *,
        tools: CodeHugToolCatalogPort,
        effective_access: EffectiveSourceAccessService,
        grants: SourceAccessEnforcementService,
        executor: CodeHugMutationExecutorPort,
        audit: CodeHugSecurityAuditPort,
    ) -> None:
        self._tools = tools
        self._effective_access = effective_access
        self._grants = grants
        self._executor = executor
        self._audit = audit

    def execute(self, command: CodeHugMutationCommand) -> Mapping[str, object]:
        tool = self._tools.resolve(tool_id=command.tool_id)
        if (
            tool is None
            or tool.tool_id != command.tool_id
            or not tool.enabled
            or not tool.mutating
        ):
            raise CodeHugMutationAuthorizationError(
                "codehug_tool_not_authorized"
            )
        binding_digest: str | None = None
        try:
            policy_decision = self._effective_access.verify_dispatch(
                tenant_id=command.tenant_id,
                project_id=command.project_id,
                source_revision_id=command.source_revision_id,
                destination_id=command.destination_id,
                operation=tool.operation,
                transformation=command.transformation,
                purpose=command.purpose,
                expected_revision_digest=command.expected_revision_digest,
                expected_policy_digest=command.expected_policy_digest,
            )
            authorized = self._grants.authorize(
                SourceAccessRequest(
                    tenant_id=command.tenant_id,
                    project_id=command.project_id,
                    source_revision_id=command.source_revision_id,
                    destination_id=command.destination_id,
                    operation=tool.operation,
                    transformation=command.transformation,
                    purpose=command.purpose,
                    policy_version=policy_decision.policy_digest,
                    manifest_id=command.content_manifest_id,
                    manifest_digest=command.content_manifest_digest,
                    assignment_id=command.assignment_id,
                    lease_id=command.lease_id,
                    destination_digest=command.destination_digest,
                    source_revision_digest=(
                        command.expected_revision_digest
                    ),
                    source_access_grant_id=(
                        command.source_access_grant_id
                    ),
                    source_access_grant_digest=(
                        command.source_access_grant_digest
                    ),
                    policy_digest=command.expected_policy_digest,
                )
            )
            binding_digest = authorized.decision.binding_digest
            result = dict(
                self._executor.execute(
                    tool_id=tool.tool_id,
                    payload_reference_id=command.payload_reference_id,
                    enforcement_manifest=authorized.manifest,
                )
            )
        except Exception as exc:
            reason_code = str(getattr(exc, "reason_code", "") or "codehug_denied")
            self._audit.record(
                actor_id=command.actor_id,
                tenant_id=command.tenant_id,
                project_id=command.project_id,
                source_revision_id=command.source_revision_id,
                destination_id=command.destination_id,
                tool_id=command.tool_id,
                operation=tool.operation.value,
                transformation=command.transformation.value,
                decision="deny",
                reason_code=reason_code,
                binding_digest=binding_digest,
            )
            raise CodeHugMutationAuthorizationError(reason_code) from exc
        self._audit.record(
            actor_id=command.actor_id,
            tenant_id=command.tenant_id,
            project_id=command.project_id,
            source_revision_id=command.source_revision_id,
            destination_id=command.destination_id,
            tool_id=command.tool_id,
            operation=tool.operation.value,
            transformation=command.transformation.value,
            decision="allow",
            reason_code="codehug_grant_match",
            binding_digest=binding_digest,
        )
        return {
            "schema": "ananta.codehug.mutation-result.v1",
            "status": str(result.get("status") or "accepted"),
            "operation_id": result.get("operation_id"),
            "binding_digest": binding_digest,
        }
