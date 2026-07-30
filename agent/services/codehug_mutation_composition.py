"""Hub-owned composition for approved CodeHug mutation intents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Protocol

from agent.services.codehug_mutation_authorization import (
    CodeHugMutationAuthorizationService,
    CodeHugMutationCommand,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantTransformation,
)


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CodeHugMutationCompositionError(ValueError):
    def __init__(
        self,
        reason_code: str,
        *,
        status_code: int = 400,
    ) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class RegisteredCodeHugMutationIntent:
    intent_id: str
    tenant_id: str
    project_id: str
    actor_id: str
    job_id: str
    tool_id: str
    operation: GrantOperation
    source_revision_id: str
    destination_id: str
    transformation: GrantTransformation
    purpose: str
    approval_id: str
    assignment_id: str
    lease_id: str
    payload_reference_id: str
    source_access_grant_id: str
    source_access_grant_digest: str
    state: str = "approved"


@dataclass(frozen=True)
class CodeHugRevisionBinding:
    source_revision_id: str
    revision_digest: str
    policy_digest: str
    content_manifest_id: str
    content_manifest_digest: str
    source_access_grant_id: str
    source_access_grant_digest: str


@dataclass(frozen=True)
class CodeHugDestinationBinding:
    destination_id: str
    destination_digest: str


class CodeHugMutationIntentCatalogPort(Protocol):
    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        intent_id: str,
    ) -> RegisteredCodeHugMutationIntent | None: ...


class CodeHugRevisionCatalogPort(Protocol):
    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
        intent: RegisteredCodeHugMutationIntent,
    ) -> CodeHugRevisionBinding | None: ...


class CodeHugDestinationCatalogPort(Protocol):
    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        destination_id: str,
    ) -> CodeHugDestinationBinding | None: ...


class CodeHugApprovalStorePort(Protocol):
    def consume(
        self,
        *,
        approval_id: str,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        intent_id: str,
        source_revision_id: str,
        destination_id: str,
        tool_id: str,
        transformation: str,
    ) -> bool: ...


class CodeHugMutationCompositionService:
    """Resolve every security binding in the Hub before authorization."""

    def __init__(
        self,
        *,
        intents: CodeHugMutationIntentCatalogPort,
        revisions: CodeHugRevisionCatalogPort,
        destinations: CodeHugDestinationCatalogPort,
        approvals: CodeHugApprovalStorePort,
        authorization: CodeHugMutationAuthorizationService,
    ) -> None:
        self._intents = intents
        self._revisions = revisions
        self._destinations = destinations
        self._approvals = approvals
        self._authorization = authorization

    def execute(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        mutation_intent_id: str,
    ) -> Mapping[str, object]:
        self._require_id("mutation_intent_id", mutation_intent_id)
        intent = self._intents.resolve(
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            intent_id=mutation_intent_id,
        )
        if intent is None or (
            intent.intent_id,
            intent.tenant_id,
            intent.project_id,
            intent.actor_id,
        ) != (
            mutation_intent_id,
            tenant_id,
            project_id,
            actor_id,
        ):
            raise CodeHugMutationCompositionError(
                "codehug_mutation_intent_not_found",
                status_code=404,
            )
        if intent.state != "approved":
            raise CodeHugMutationCompositionError(
                "codehug_mutation_intent_not_approved",
                status_code=403,
            )
        revision = self._revisions.resolve(
            tenant_id=tenant_id,
            project_id=project_id,
            source_revision_id=intent.source_revision_id,
            intent=intent,
        )
        destination = self._destinations.resolve(
            tenant_id=tenant_id,
            project_id=project_id,
            destination_id=intent.destination_id,
        )
        if (
            revision is None
            or revision.source_revision_id != intent.source_revision_id
        ):
            raise CodeHugMutationCompositionError(
                "source_revision_not_found", status_code=404
            )
        if (
            destination is None
            or destination.destination_id != intent.destination_id
        ):
            raise CodeHugMutationCompositionError(
                "destination_not_found", status_code=404
            )
        self._validate_bindings(intent, revision, destination)
        if self._approvals.consume(
            approval_id=intent.approval_id,
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            intent_id=intent.intent_id,
            source_revision_id=intent.source_revision_id,
            destination_id=intent.destination_id,
            tool_id=intent.tool_id,
            transformation=intent.transformation.value,
        ) is not True:
            raise CodeHugMutationCompositionError(
                "codehug_mutation_approval_required",
                status_code=403,
            )
        return self._authorization.execute(
            CodeHugMutationCommand(
                tenant_id=tenant_id,
                project_id=project_id,
                actor_id=actor_id,
                source_revision_id=revision.source_revision_id,
                destination_id=destination.destination_id,
                destination_digest=destination.destination_digest,
                expected_revision_digest=revision.revision_digest,
                expected_policy_digest=revision.policy_digest,
                tool_id=intent.tool_id,
                transformation=intent.transformation,
                purpose=intent.purpose,
                content_manifest_id=revision.content_manifest_id,
                content_manifest_digest=revision.content_manifest_digest,
                assignment_id=intent.assignment_id,
                lease_id=intent.lease_id,
                payload_reference_id=intent.payload_reference_id,
                source_access_grant_id=(
                    revision.source_access_grant_id
                ),
                source_access_grant_digest=(
                    revision.source_access_grant_digest
                ),
            )
        )

    @staticmethod
    def _require_id(name: str, value: str) -> None:
        if _OPAQUE_ID.fullmatch(str(value or "")) is None:
            raise CodeHugMutationCompositionError(f"{name}_invalid")

    def _validate_bindings(
        self,
        intent: RegisteredCodeHugMutationIntent,
        revision: CodeHugRevisionBinding,
        destination: CodeHugDestinationBinding,
    ) -> None:
        for name, value in (
            ("tool_id", intent.tool_id),
            ("job_id", intent.job_id),
            ("source_revision_id", intent.source_revision_id),
            ("destination_id", intent.destination_id),
            ("approval_id", intent.approval_id),
            ("assignment_id", intent.assignment_id),
            ("lease_id", intent.lease_id),
            ("payload_reference_id", intent.payload_reference_id),
            ("content_manifest_id", revision.content_manifest_id),
            (
                "source_access_grant_id",
                revision.source_access_grant_id,
            ),
        ):
            self._require_id(name, value)
        for name, value in (
            ("revision_digest", revision.revision_digest),
            ("policy_digest", revision.policy_digest),
            ("content_manifest_digest", revision.content_manifest_digest),
            ("destination_digest", destination.destination_digest),
            (
                "source_access_grant_digest",
                revision.source_access_grant_digest,
            ),
        ):
            if _SHA256.fullmatch(str(value or "")) is None:
                raise CodeHugMutationCompositionError(f"{name}_invalid")


__all__ = [
    "CodeHugApprovalStorePort",
    "CodeHugDestinationBinding",
    "CodeHugDestinationCatalogPort",
    "CodeHugMutationCompositionError",
    "CodeHugMutationCompositionService",
    "CodeHugMutationIntentCatalogPort",
    "CodeHugRevisionBinding",
    "CodeHugRevisionCatalogPort",
    "RegisteredCodeHugMutationIntent",
]
