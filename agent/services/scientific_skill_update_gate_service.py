"""Deterministic compatibility/security gate for scientific skill pin updates."""

from __future__ import annotations

from dataclasses import dataclass

from agent.services.scientific_skill_catalog_service import (
    ScientificSkillCatalogEntry,
    ScientificSkillCatalogEntryStatus,
)
from agent.services.scientific_skill_manifest_service import ScientificSkillManifest
from agent.services.scientific_skill_risk_profile_service import (
    ScientificSkillOperatingMode,
    ScientificSkillRiskProfile,
)

_MODE_ORDER = {
    ScientificSkillOperatingMode.DOCUMENTATION_ONLY: 0,
    ScientificSkillOperatingMode.READ_ONLY_RESEARCH: 1,
    ScientificSkillOperatingMode.CONTROLLED_EXECUTION: 2,
    ScientificSkillOperatingMode.BLOCKED: 3,
}


@dataclass(frozen=True)
class ScientificSkillUpdateDiff:
    old_pin: str
    new_pin: str
    added_files: tuple[str, ...]
    removed_files: tuple[str, ...]
    modified_files: tuple[str, ...]
    added_capabilities: tuple[str, ...]
    added_dependencies: tuple[str, ...]
    added_network_targets: tuple[str, ...]
    added_credential_requirements: tuple[str, ...]
    old_mode: str
    new_mode: str


@dataclass(frozen=True)
class ScientificSkillUpdateGateReport:
    decision: str
    retained_approved_pin: str
    candidate_pin: str
    blockers: tuple[str, ...]
    visible_diff: ScientificSkillUpdateDiff
    automatic_adoption_performed: bool = False


class ScientificSkillUpdateGateService:
    def evaluate(
        self,
        *,
        approved_entry: ScientificSkillCatalogEntry,
        current_manifest: ScientificSkillManifest,
        current_profile: ScientificSkillRiskProfile,
        candidate_manifest: ScientificSkillManifest,
        candidate_profile: ScientificSkillRiskProfile,
    ) -> ScientificSkillUpdateGateReport:
        if (
            approved_entry.status is not ScientificSkillCatalogEntryStatus.APPROVED
            or approved_entry.upstream_pin != current_manifest.upstream_pin
            or approved_entry.skill_sha256 != current_manifest.sha256
            or approved_entry.risk_profile_digest != current_profile.profile_digest
            or current_profile.skill_sha256 != current_manifest.sha256
            or candidate_profile.skill_sha256 != candidate_manifest.sha256
            or candidate_manifest.name != current_manifest.name
            or candidate_manifest.upstream_path != current_manifest.upstream_path
        ):
            raise ValueError("scientific_skill_update_binding_invalid")
        current_files = {item.relative_path: item for item in current_manifest.declared_files}
        candidate_files = {item.relative_path: item for item in candidate_manifest.declared_files}
        added_files = tuple(sorted(set(candidate_files) - set(current_files)))
        removed_files = tuple(sorted(set(current_files) - set(candidate_files)))
        modified_files = tuple(
            sorted(
                path
                for path in set(current_files) & set(candidate_files)
                if current_files[path].sha256 != candidate_files[path].sha256
            )
        )
        diff = ScientificSkillUpdateDiff(
            old_pin=current_manifest.upstream_pin,
            new_pin=candidate_manifest.upstream_pin,
            added_files=added_files,
            removed_files=removed_files,
            modified_files=modified_files,
            added_capabilities=tuple(
                sorted(
                    set(candidate_profile.detected_capabilities)
                    - set(current_profile.detected_capabilities)
                )
            ),
            added_dependencies=tuple(
                sorted(
                    item.declaration
                    for item in set(candidate_profile.dependencies)
                    - set(current_profile.dependencies)
                )
            ),
            added_network_targets=tuple(
                sorted(
                    set(candidate_profile.network_targets)
                    - set(current_profile.network_targets)
                )
            ),
            added_credential_requirements=tuple(
                sorted(
                    set(candidate_profile.credential_requirements)
                    - set(current_profile.credential_requirements)
                )
            ),
            old_mode=current_profile.operating_mode.value,
            new_mode=candidate_profile.operating_mode.value,
        )
        blockers: set[str] = set()
        if any(candidate_files[path].kind == "script" for path in added_files):
            blockers.add("scientific_skill_update_new_script")
        if any(candidate_files[path].kind == "script" for path in modified_files):
            blockers.add("scientific_skill_update_modified_script")
        if diff.added_capabilities:
            blockers.add("scientific_skill_update_capability_expansion")
        if diff.added_dependencies:
            blockers.add("scientific_skill_update_dependency_expansion")
        if diff.added_network_targets:
            blockers.add("scientific_skill_update_network_expansion")
        if diff.added_credential_requirements:
            blockers.add("scientific_skill_update_credential_expansion")
        if _MODE_ORDER[candidate_profile.operating_mode] > _MODE_ORDER[current_profile.operating_mode]:
            blockers.add("scientific_skill_update_rights_expansion")
        if candidate_profile.operating_mode is ScientificSkillOperatingMode.BLOCKED:
            blockers.add("scientific_skill_update_candidate_blocked")
        return ScientificSkillUpdateGateReport(
            decision="blocked" if blockers else "proposal_allowed",
            retained_approved_pin=approved_entry.upstream_pin,
            candidate_pin=candidate_manifest.upstream_pin,
            blockers=tuple(sorted(blockers)),
            visible_diff=diff,
        )


__all__ = [
    "ScientificSkillUpdateDiff",
    "ScientificSkillUpdateGateReport",
    "ScientificSkillUpdateGateService",
]
