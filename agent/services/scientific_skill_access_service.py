"""Visibility boundary for scientific skill metadata and projections."""

from __future__ import annotations

from dataclasses import dataclass

from agent.services.scientific_skill_catalog_service import ScientificSkillCatalogEntry
from agent.services.source_control_access_policy import HubSourcePrincipal, SourceObjectBinding


@dataclass(frozen=True)
class ScientificSkillCatalogView:
    entry_id: str
    skill_name: str
    allowed_mode: str
    upstream_pin: str | None
    dependencies: tuple[str, ...] | None
    credential_requirements: tuple[str, ...] | None


class ScientificSkillAccessService:
    """Expose sensitive skill metadata only to a scoped project owner/admin."""

    def project(
        self,
        *,
        principal: HubSourcePrincipal,
        binding: SourceObjectBinding,
        entry: ScientificSkillCatalogEntry,
        dependencies: tuple[str, ...],
        credential_requirements: tuple[str, ...],
    ) -> ScientificSkillCatalogView | None:
        if not _same_scope(principal, binding):
            return None
        privileged = principal.is_admin or principal.is_project_owner
        return ScientificSkillCatalogView(
            entry_id=entry.entry_id,
            skill_name=entry.skill_name,
            allowed_mode=entry.allowed_mode.value,
            upstream_pin=entry.upstream_pin if privileged else None,
            dependencies=tuple(sorted(dependencies)) if privileged else None,
            credential_requirements=tuple(sorted(credential_requirements)) if privileged else None,
        )


def _same_scope(principal: HubSourcePrincipal, binding: SourceObjectBinding) -> bool:
    return bool(
        binding.exists
        and binding.is_scoped
        and (principal.is_admin or (principal.tenant_id, principal.project_id) == (binding.tenant_id, binding.project_id))
    )


__all__ = ["ScientificSkillAccessService", "ScientificSkillCatalogView"]
