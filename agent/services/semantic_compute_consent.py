"""Current Hub authority for semantic-compute scheduling consent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent.repositories.semantic_contract_repository import (
    SemanticContractRepository,
    SemanticContractRepositoryError,
    SemanticPrincipal,
)
from agent.services.semantic_media_permission_service import (
    SemanticMediaPermissionService,
)
from ananta_contracts.semantic_compute import (
    SemanticComputeContractError,
    validate_quality_contract,
)

_CONTROL_DATA_TYPE = "application/vnd.ananta.semantic-media-control+json"
_CONTROL_PURPOSE = "semantic_media_control"


@dataclass(frozen=True, slots=True)
class ComputeConsentContext:
    """Minimum facts needed to revalidate one candidate-role decision."""

    tenant_id: str
    owner_subject: str
    contract_id: str
    contract_digest: str
    session_id: str
    room_id: str | None
    epoch: int
    candidate_id: str
    task_type: str
    role: str


class SemanticComputeConsentAuthorityPort(Protocol):
    """Current consent decision; advertisements are deliberately not inputs."""

    def authorized(self, context: ComputeConsentContext) -> bool: ...


class DenySemanticComputeConsentAuthority:
    """Fail-closed default used when no Hub authority was composed."""

    def authorized(self, _context: ComputeConsentContext) -> bool:
        return False


class CapabilityGrantComputeConsentAuthority:
    """Revalidate the candidate's persisted, revocable compute grant."""

    def __init__(self, permissions: SemanticMediaPermissionService) -> None:
        self._permissions = permissions

    def authorized(self, context: ComputeConsentContext) -> bool:
        scope_kind = "room" if context.room_id is not None else "session"
        scope_id = context.room_id or context.session_id
        try:
            records = self._permissions.list_scope(
                tenant_id=context.tenant_id,
                scope_kind=scope_kind,
                scope_id=scope_id,
                epoch=context.epoch,
                subject_id=context.candidate_id,
                limit=200,
            )
        except Exception:
            return False
        for record in records:
            allowed, _reason = self._permissions.evaluate(
                record.grant,
                capability="compute",
                tenant_id=context.tenant_id,
                subject_id=context.candidate_id,
                scope_kind=scope_kind,
                scope_id=scope_id,
                direction="egress",
                data_type=_CONTROL_DATA_TYPE,
                purpose=_CONTROL_PURPOSE,
                epoch=context.epoch,
            )
            if allowed:
                return True
        return False


class TrustedServerComputeConsentAuthority:
    """Revalidate the owner's active Trusted-Compute contract from Hub state."""

    def __init__(self, contracts: SemanticContractRepository) -> None:
        self._contracts = contracts

    def authorized(self, context: ComputeConsentContext) -> bool:
        principal = SemanticPrincipal(context.tenant_id, context.owner_subject)
        try:
            self._contracts.require_membership(
                principal,
                session_id=context.session_id,
                epoch=context.epoch,
                permission="semantic_compute",
            )
            contract = self._contracts.get(principal, context.contract_id)
            payload = validate_quality_contract(contract.contract_payload)
        except (
            SemanticComputeContractError,
            SemanticContractRepositoryError,
        ):
            return False
        return bool(
            contract.status == "active"
            and contract.digest == context.contract_digest
            and contract.session_id == context.session_id
            and contract.room_id == context.room_id
            and contract.epoch == context.epoch
            and int(payload["consent_version"]) >= 1
            and payload["security_mode"] == "trusted_compute"
            and payload["trusted_compute_grant"] is True
            and context.task_type in set(payload["task_types"])
        )


__all__ = [
    "CapabilityGrantComputeConsentAuthority",
    "ComputeConsentContext",
    "DenySemanticComputeConsentAuthority",
    "SemanticComputeConsentAuthorityPort",
    "TrustedServerComputeConsentAuthority",
]
