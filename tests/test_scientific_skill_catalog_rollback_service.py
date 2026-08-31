from __future__ import annotations

import inspect

import pytest

import agent.services.scientific_skill_catalog_rollback_service as rollback_module
from agent.services.scientific_skill_catalog_rollback_service import (
    ScientificSkillCatalogRollbackService,
)
from agent.services.scientific_skill_catalog_service import (
    ScientificSkillApprovalLevel,
    ScientificSkillCatalog,
    ScientificSkillCatalogEntry,
    ScientificSkillCatalogEntryStatus,
    ScientificSkillCatalogError,
    ScientificSkillNetworkProfile,
)
from agent.services.scientific_skill_manifest_service import ScientificSkillManifest
from agent.services.scientific_skill_risk_profile_service import (
    ScientificSkillOperatingMode,
    ScientificSkillRiskProfile,
)


class _Store:
    def __init__(self) -> None:
        self.catalogs: list[ScientificSkillCatalog] = []

    def latest(self, *, catalog_id: str) -> ScientificSkillCatalog | None:
        matches = [item for item in self.catalogs if item.catalog_id == catalog_id]
        return matches[-1] if matches else None

    def get(self, *, catalog_id: str, catalog_version: str) -> ScientificSkillCatalog | None:
        return next(
            (
                item
                for item in self.catalogs
                if (item.catalog_id, item.catalog_version) == (catalog_id, catalog_version)
            ),
            None,
        )

    def append(self, catalog: ScientificSkillCatalog) -> ScientificSkillCatalog:
        self.catalogs.append(catalog)
        return catalog

    def append_if_latest(
        self,
        catalog: ScientificSkillCatalog,
        *,
        expected_latest_digest: str,
    ) -> ScientificSkillCatalog:
        latest = self.latest(catalog_id=catalog.catalog_id)
        if latest is None or latest.catalog_digest != expected_latest_digest:
            raise ScientificSkillCatalogError(
                "scientific_skill_catalog_rollback_revision_conflict"
            )
        return self.append(catalog)


def _binding(pin: str, marker: str):
    manifest = ScientificSkillManifest(
        name="astropy",
        description="Pinned documentation",
        upstream_path="skills/astropy/SKILL.md",
        upstream_pin=pin,
        sha256=marker * 64,
        license="MIT",
        declared_files=(),
        declared_capabilities=(),
        declared_dependencies=(),
        declared_data_classification="internal",
        source_references=(),
        parser_warnings=(),
    )
    profile = ScientificSkillRiskProfile(
        profile_version="ananta.scientific-skill-risk.v1",
        skill_name="astropy",
        skill_sha256=manifest.sha256,
        operating_mode=ScientificSkillOperatingMode.DOCUMENTATION_ONLY,
        detected_capabilities=(),
        dependencies=(),
        network_targets=(),
        credential_requirements=(),
        data_classification="internal",
        context_budget_tokens=100,
        reason_codes=(),
        manual_assessment_id=None,
        profile_digest=("c" if marker != "c" else "d") * 64,
    )
    entry = ScientificSkillCatalogEntry.create(
        skill_name=manifest.name,
        upstream_path=manifest.upstream_path,
        upstream_pin=manifest.upstream_pin,
        skill_sha256=manifest.sha256,
        risk_profile_digest=profile.profile_digest,
        status=ScientificSkillCatalogEntryStatus.APPROVED,
        allowed_mode=ScientificSkillOperatingMode.DOCUMENTATION_ONLY,
        context_budget_tokens=100,
        allowed_tools=(),
        data_classification="internal",
        network_profile=ScientificSkillNetworkProfile.DENIED,
        allowed_network_targets=(),
        approval_level=ScientificSkillApprovalLevel.NONE,
        approval_receipt_digest="e" * 64,
    )
    return entry, manifest, profile


def _catalog(version: str, entry: ScientificSkillCatalogEntry) -> ScientificSkillCatalog:
    return ScientificSkillCatalog.create(
        catalog_id="scientific-skills-pilot",
        catalog_version=version,
        feature_enabled=True,
        entries=(entry,),
    )


def test_rollback_appends_old_approved_pin_without_mutating_history() -> None:
    old_entry, old_manifest, old_profile = _binding("0123456789abcdef", "a")
    current_entry, _, _ = _binding("fedcba9876543210", "b")
    old = _catalog("v1", old_entry)
    current = _catalog("v2", current_entry)
    store = _Store()
    store.append(old)
    store.append(current)

    rolled_back = ScientificSkillCatalogRollbackService(store).rollback(
        catalog_id=current.catalog_id,
        target_catalog_version="v1",
        new_catalog_version="v3",
        skill_name="astropy",
        expected_current_digest=current.catalog_digest,
        bindings={old_entry.entry_id: (old_manifest, old_profile)},
    )

    assert rolled_back.catalog_version == "v3"
    assert rolled_back.entries == (old_entry,)
    assert store.get(catalog_id=current.catalog_id, catalog_version="v1") == old
    assert store.get(catalog_id=current.catalog_id, catalog_version="v2") == current
    assert len(store.catalogs) == 3


def test_rollback_rejects_stale_current_revision_and_unapproved_target() -> None:
    old_entry, old_manifest, old_profile = _binding("0123456789abcdef", "a")
    current_entry, _, _ = _binding("fedcba9876543210", "b")
    store = _Store()
    store.append(_catalog("v1", old_entry))
    current = store.append(_catalog("v2", current_entry))
    service = ScientificSkillCatalogRollbackService(store)
    with pytest.raises(ScientificSkillCatalogError, match="revision_conflict"):
        service.rollback(
            catalog_id=current.catalog_id,
            target_catalog_version="v1",
            new_catalog_version="v3",
            skill_name="astropy",
            expected_current_digest="f" * 64,
            bindings={old_entry.entry_id: (old_manifest, old_profile)},
        )


def test_rollback_boundary_cannot_touch_source_knowledge_qdrant_or_codecompass() -> None:
    source = inspect.getsource(rollback_module)
    assert "source_catalog" not in source
    assert "knowledge" not in source
    assert "qdrant" not in source.casefold()
    assert "codecompass" not in source.casefold()
