"""Visibility boundary for scientific skill metadata and projections."""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent.services.scientific_skill_catalog_service import ScientificSkillCatalogEntry
from agent.services.source_control_access_policy import HubSourcePrincipal, SourceObjectBinding

_SECRET_REFERENCE = re.compile(r"^secret:[A-Za-z0-9][A-Za-z0-9_.:/-]{0,190}$")


class ScientificSkillAccessError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class ScientificSkillCatalogView:
    entry_id: str
    skill_name: str
    allowed_mode: str
    content: str | None
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
        content: str,
        dependencies: tuple[str, ...],
        credential_requirements: tuple[str, ...],
    ) -> ScientificSkillCatalogView | None:
        if not _same_scope(principal, binding):
            return None
        if any(_SECRET_REFERENCE.fullmatch(reference) is None for reference in credential_requirements):
            raise ScientificSkillAccessError("scientific_skill_credential_reference_invalid")
        privileged = principal.is_admin or principal.is_project_owner
        return ScientificSkillCatalogView(
            entry_id=entry.entry_id,
            skill_name=entry.skill_name,
            allowed_mode=entry.allowed_mode.value,
            content=content if privileged else None,
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


__all__ = [
    "ScientificSkillAccessError",
    "ScientificSkillAccessService",
    "ScientificSkillCatalogView",
]
