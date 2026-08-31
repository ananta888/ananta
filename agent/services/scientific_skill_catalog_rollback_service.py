"""Append-only rollback revisions for the scientific skill catalog."""

from __future__ import annotations

from typing import Protocol

from agent.services.scientific_skill_catalog_service import (
    ScientificSkillCatalog,
    ScientificSkillCatalogEntryStatus,
    ScientificSkillCatalogError,
    ScientificSkillCatalogService,
)
from agent.services.scientific_skill_manifest_service import ScientificSkillManifest
from agent.services.scientific_skill_risk_profile_service import ScientificSkillRiskProfile


class ScientificSkillCatalogRollbackStorePort(Protocol):
    def latest(self, *, catalog_id: str) -> ScientificSkillCatalog | None: ...

    def get(
        self,
        *,
        catalog_id: str,
        catalog_version: str,
    ) -> ScientificSkillCatalog | None: ...

    def append_if_latest(
        self,
        catalog: ScientificSkillCatalog,
        *,
        expected_latest_digest: str,
    ) -> ScientificSkillCatalog: ...


class ScientificSkillCatalogRollbackService:
    """Restore one historical approved pin by appending a catalog revision."""

    def __init__(self, store: ScientificSkillCatalogRollbackStorePort) -> None:
        self._store = store

    def rollback(
        self,
        *,
        catalog_id: str,
        target_catalog_version: str,
        new_catalog_version: str,
        skill_name: str,
        expected_current_digest: str,
        bindings: dict[str, tuple[ScientificSkillManifest, ScientificSkillRiskProfile]],
    ) -> ScientificSkillCatalog:
        current = self._store.latest(catalog_id=catalog_id)
        target = self._store.get(
            catalog_id=catalog_id,
            catalog_version=target_catalog_version,
        )
        if current is None or target is None:
            raise ScientificSkillCatalogError("scientific_skill_catalog_rollback_revision_not_found")
        if current.catalog_digest != expected_current_digest:
            raise ScientificSkillCatalogError("scientific_skill_catalog_rollback_revision_conflict")
        if self._store.get(catalog_id=catalog_id, catalog_version=new_catalog_version) is not None:
            raise ScientificSkillCatalogError("scientific_skill_catalog_version_conflict")
        matches = tuple(
            entry
            for entry in target.entries
            if entry.skill_name == skill_name
            and entry.status is ScientificSkillCatalogEntryStatus.APPROVED
        )
        if len(matches) != 1:
            raise ScientificSkillCatalogError("scientific_skill_catalog_rollback_pin_not_approved")
        restored = matches[0]
        entries = tuple(entry for entry in current.entries if entry.skill_name != skill_name) + (restored,)
        rollback = ScientificSkillCatalog.create(
            catalog_id=catalog_id,
            catalog_version=new_catalog_version,
            feature_enabled=current.feature_enabled,
            entries=entries,
        )
        if set(bindings) != {entry.entry_id for entry in rollback.entries}:
            raise ScientificSkillCatalogError("scientific_skill_catalog_binding_set_mismatch")
        for entry in rollback.entries:
            manifest, profile = bindings[entry.entry_id]
            ScientificSkillCatalogService.validate_binding(
                entry,
                manifest=manifest,
                profile=profile,
            )
        return self._store.append_if_latest(
            rollback,
            expected_latest_digest=expected_current_digest,
        )


__all__ = [
    "ScientificSkillCatalogRollbackService",
    "ScientificSkillCatalogRollbackStorePort",
]
