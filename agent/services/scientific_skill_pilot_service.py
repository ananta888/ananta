"""Hub-owned visibility and selection boundary for the scientific-skill pilot."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from agent.services.scientific_skill_adapter_service import (
    ScientificSkillAdapterOutcome,
    ScientificSkillAdapterRequest,
    ScientificSkillAdapterService,
)
from agent.services.scientific_skill_catalog_service import (
    ScientificSkillCatalog,
    ScientificSkillCatalogEntry,
    ScientificSkillCatalogEntryStatus,
)
from agent.services.source_control_access_policy import (
    HubSourcePrincipal,
    SourceControlAccessPolicy,
    SourceObjectBinding,
)

SCIENTIFIC_SKILL_PILOT_ROLE = "scientific_skill_pilot"
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,190}$")


@dataclass(frozen=True)
class ScientificSkillPilotCard:
    entry_id: str
    skill_name: str
    upstream_repository: str
    upstream_path: str
    upstream_pin: str
    skill_sha256: str
    allowed_mode: str
    context_budget_tokens: int
    allowed_tools: tuple[str, ...]
    network_profile: str
    approval_level: str
    approval_status: str
    source_reference: str


@dataclass(frozen=True)
class ScientificSkillPilotAuditEvent:
    task_id: str
    subject_id: str
    tenant_id: str
    project_id: str
    catalog_digest: str
    skill_name: str
    entry_id: str | None
    selection_status: str
    reason_code: str


class ScientificSkillPilotAuditPort(Protocol):
    def emit(self, event: ScientificSkillPilotAuditEvent) -> bool: ...


class ScientificSkillPilotService:
    """Expose and select admitted skills only after flag and scope authorization."""

    def __init__(
        self,
        *,
        adapter_service: ScientificSkillAdapterService,
        audit_port: ScientificSkillPilotAuditPort,
        access_policy: SourceControlAccessPolicy | None = None,
    ) -> None:
        self._adapter_service = adapter_service
        self._audit_port = audit_port
        self._access_policy = access_policy or SourceControlAccessPolicy()

    def available(
        self,
        *,
        catalog: ScientificSkillCatalog,
        principal: HubSourcePrincipal,
        binding: SourceObjectBinding,
    ) -> tuple[ScientificSkillPilotCard, ...]:
        if not self._authorized(catalog=catalog, principal=principal, binding=binding):
            return ()
        return tuple(
            _card(entry)
            for entry in sorted(catalog.entries, key=lambda item: item.skill_name)
            if entry.status is ScientificSkillCatalogEntryStatus.APPROVED
        )

    def select(
        self,
        *,
        catalog: ScientificSkillCatalog,
        principal: HubSourcePrincipal,
        binding: SourceObjectBinding,
        request: ScientificSkillAdapterRequest,
        task_id: str,
    ) -> ScientificSkillAdapterOutcome:
        if not isinstance(task_id, str) or _TASK_ID.fullmatch(task_id) is None:
            return ScientificSkillAdapterOutcome.degraded("scientific_skill_pilot_task_invalid")
        cards = self.available(catalog=catalog, principal=principal, binding=binding)
        card = next((item for item in cards if item.skill_name == request.skill_name), None)
        if card is None:
            reason_code = self._rejection_reason(
                catalog=catalog,
                principal=principal,
                binding=binding,
            )
            self._emit(
                catalog=catalog,
                principal=principal,
                request=request,
                task_id=task_id,
                entry_id=None,
                status="rejected",
                reason_code=reason_code,
            )
            return ScientificSkillAdapterOutcome.degraded(reason_code)

        outcome = self._adapter_service.adapt(request)
        selected = outcome.projection is not None and outcome.degradation_code is None
        reason_code = "scientific_skill_pilot_selected" if selected else (
            outcome.degradation_code or "scientific_skill_pilot_adapter_rejected"
        )
        audited = self._emit(
            catalog=catalog,
            principal=principal,
            request=request,
            task_id=task_id,
            entry_id=card.entry_id,
            status="selected" if selected else "rejected",
            reason_code=reason_code,
        )
        if not audited:
            return ScientificSkillAdapterOutcome.degraded("scientific_skill_pilot_audit_required")
        return outcome

    def _authorized(
        self,
        *,
        catalog: ScientificSkillCatalog,
        principal: HubSourcePrincipal,
        binding: SourceObjectBinding,
    ) -> bool:
        role_granted = principal.is_admin or SCIENTIFIC_SKILL_PILOT_ROLE in principal.roles
        return bool(
            catalog.feature_enabled
            and role_granted
            and self._access_policy.can_view(principal=principal, binding=binding)
        )

    def _rejection_reason(
        self,
        *,
        catalog: ScientificSkillCatalog,
        principal: HubSourcePrincipal,
        binding: SourceObjectBinding,
    ) -> str:
        if not catalog.feature_enabled:
            return "scientific_skill_pilot_feature_disabled"
        if not self._authorized(catalog=catalog, principal=principal, binding=binding):
            return "scientific_skill_pilot_access_denied"
        return "scientific_skill_pilot_not_admitted"

    def _emit(
        self,
        *,
        catalog: ScientificSkillCatalog,
        principal: HubSourcePrincipal,
        request: ScientificSkillAdapterRequest,
        task_id: str,
        entry_id: str | None,
        status: str,
        reason_code: str,
    ) -> bool:
        if principal.tenant_id is None or principal.project_id is None:
            return False
        return self._audit_port.emit(
            ScientificSkillPilotAuditEvent(
                task_id=task_id,
                subject_id=principal.subject_id,
                tenant_id=principal.tenant_id,
                project_id=principal.project_id,
                catalog_digest=catalog.catalog_digest,
                skill_name=request.skill_name,
                entry_id=entry_id,
                selection_status=status,
                reason_code=reason_code,
            )
        )


def _card(entry: ScientificSkillCatalogEntry) -> ScientificSkillPilotCard:
    return ScientificSkillPilotCard(
        entry_id=entry.entry_id,
        skill_name=entry.skill_name,
        upstream_repository=entry.upstream_repository,
        upstream_path=entry.upstream_path,
        upstream_pin=entry.upstream_pin,
        skill_sha256=entry.skill_sha256,
        allowed_mode=entry.allowed_mode.value,
        context_budget_tokens=entry.context_budget_tokens,
        allowed_tools=entry.allowed_tools,
        network_profile=entry.network_profile.value,
        approval_level=entry.approval_level.value,
        approval_status=entry.status.value,
        source_reference=(
            f"{entry.upstream_repository}/blob/{entry.upstream_pin}/{entry.upstream_path}"
        ),
    )


__all__ = [
    "SCIENTIFIC_SKILL_PILOT_ROLE",
    "ScientificSkillPilotAuditEvent",
    "ScientificSkillPilotAuditPort",
    "ScientificSkillPilotCard",
    "ScientificSkillPilotService",
]
