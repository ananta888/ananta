from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent.services.scientific_skill_catalog_service import (
    ScientificSkillApprovalLevel,
    ScientificSkillCatalog,
    ScientificSkillCatalogEntry,
    ScientificSkillCatalogEntryStatus,
    ScientificSkillCatalogError,
    ScientificSkillCatalogService,
    ScientificSkillNetworkProfile,
)
from agent.services.scientific_skill_manifest_service import ScientificSkillManifestImporter
from agent.services.scientific_skill_risk_profile_service import (
    ScientificSkillOperatingMode,
    ScientificSkillRiskProfiler,
)


class _Store:
    def __init__(self) -> None:
        self.catalogs: list[ScientificSkillCatalog] = []

    def latest(self, *, catalog_id: str) -> ScientificSkillCatalog | None:
        matches = [catalog for catalog in self.catalogs if catalog.catalog_id == catalog_id]
        return matches[-1] if matches else None

    def get(self, *, catalog_id: str, catalog_version: str) -> ScientificSkillCatalog | None:
        return next(
            (
                catalog
                for catalog in self.catalogs
                if catalog.catalog_id == catalog_id and catalog.catalog_version == catalog_version
            ),
            None,
        )

    def append(self, catalog: ScientificSkillCatalog) -> ScientificSkillCatalog:
        self.catalogs.append(catalog)
        return catalog


def _research_evidence(root: Path):
    skill = root / "skills" / "literature"
    skill.mkdir(parents=True)
    (root / "plugin.json").write_text(json.dumps({"name": "research", "license": "MIT"}), encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: literature\ndescription: Review evidence\n---\n"
        "Review literature and citations from [source](https://papers.example.test/index).\n",
        encoding="utf-8",
    )
    package = ScientificSkillManifestImporter().inspect(
        package_path=root,
        upstream_repository="https://github.com/K-Dense-AI/scientific-agent-skills",
        upstream_pin="0123456789abcdef0123456789abcdef01234567",
    )
    manifest = package.skills[0]
    contents = {item.relative_path: (root / item.relative_path).read_bytes() for item in manifest.declared_files}
    profile = ScientificSkillRiskProfiler().profile(manifest=manifest, declared_contents=contents)
    return manifest, profile


def _entry(
    manifest,
    profile,
    *,
    status: ScientificSkillCatalogEntryStatus = ScientificSkillCatalogEntryStatus.APPROVED,
    receipt: str | None = "e" * 64,
) -> ScientificSkillCatalogEntry:
    return ScientificSkillCatalogEntry.create(
        skill_name=manifest.name,
        upstream_path=manifest.upstream_path,
        upstream_pin=manifest.upstream_pin,
        skill_sha256=manifest.sha256,
        risk_profile_digest=profile.profile_digest,
        status=status,
        allowed_mode=ScientificSkillOperatingMode.READ_ONLY_RESEARCH,
        context_budget_tokens=profile.context_budget_tokens,
        allowed_tools=("artifact_read", "citation_search", "source_lookup"),
        data_classification=profile.data_classification,
        network_profile=ScientificSkillNetworkProfile.DECLARED_READ_ONLY,
        allowed_network_targets=profile.network_targets,
        approval_level=ScientificSkillApprovalLevel.SOURCE_ACCESS,
        approval_receipt_digest=receipt,
    )


def _catalog(
    version: str,
    entries: tuple[ScientificSkillCatalogEntry, ...],
    *,
    enabled: bool = True,
) -> ScientificSkillCatalog:
    return ScientificSkillCatalog.create(
        catalog_id="scientific-default",
        catalog_version=version,
        feature_enabled=enabled,
        entries=entries,
    )


def test_default_catalog_contains_pilot_entries_but_remains_fail_closed() -> None:
    path = Path(__file__).parents[1] / "config" / "scientific-skills-catalog.json"
    catalog = ScientificSkillCatalog.from_mapping(json.loads(path.read_text(encoding="utf-8")))
    assert tuple(entry.skill_name for entry in catalog.entries) == (
        "astropy",
        "networkx",
        "scvi-tools",
        "torch-geometric",
        "umap-learn",
    )
    assert catalog.feature_enabled is False
    with pytest.raises(ScientificSkillCatalogError, match="feature_disabled"):
        ScientificSkillCatalogService.resolve(catalog, skill_name="astropy")


def test_exact_manifest_and_profile_binding_is_required_before_publish_and_resolution(tmp_path: Path) -> None:
    manifest, profile = _research_evidence(tmp_path / "package")
    entry = _entry(manifest, profile)
    catalog = _catalog("v1", (entry,))
    service = ScientificSkillCatalogService(_Store())
    assert service.publish(catalog, bindings={entry.entry_id: (manifest, profile)}) == catalog
    assert service.resolve(catalog, skill_name="literature") == entry

    with pytest.raises(ScientificSkillCatalogError, match="binding_mismatch"):
        service.validate_binding(entry, manifest=replace(manifest, sha256="a" * 64), profile=profile)
    with pytest.raises(ScientificSkillCatalogError, match="binding_set_mismatch"):
        ScientificSkillCatalogService(_Store()).publish(catalog, bindings={})


def test_candidate_or_missing_entry_is_never_selectable(tmp_path: Path) -> None:
    manifest, profile = _research_evidence(tmp_path / "package")
    candidate = _entry(manifest, profile, status=ScientificSkillCatalogEntryStatus.CANDIDATE, receipt=None)
    catalog = _catalog("v1", (candidate,))
    ScientificSkillCatalogService(_Store()).publish(
        catalog,
        bindings={candidate.entry_id: (manifest, profile)},
    )
    with pytest.raises(ScientificSkillCatalogError, match="entry_not_admitted"):
        ScientificSkillCatalogService.resolve(catalog, skill_name="literature")
    with pytest.raises(ScientificSkillCatalogError, match="entry_not_admitted"):
        ScientificSkillCatalogService.resolve(catalog, skill_name="unknown")


def test_catalog_entry_declares_context_tools_data_network_and_approval_policy(tmp_path: Path) -> None:
    manifest, profile = _research_evidence(tmp_path / "package")
    entry = _entry(manifest, profile)
    assert entry.context_budget_tokens == profile.context_budget_tokens
    assert entry.allowed_tools == ("artifact_read", "citation_search", "source_lookup")
    assert entry.data_classification == "internal"
    assert entry.network_profile is ScientificSkillNetworkProfile.DECLARED_READ_ONLY
    assert entry.allowed_network_targets == ("papers.example.test",)
    assert entry.approval_level is ScientificSkillApprovalLevel.SOURCE_ACCESS
    assert ScientificSkillCatalogEntry.from_mapping(entry.to_mapping()) == entry

    invalid = entry.to_mapping()
    invalid["untrusted"] = True
    with pytest.raises(ScientificSkillCatalogError, match="catalog_shape_invalid"):
        ScientificSkillCatalogEntry.from_mapping(invalid)


def test_new_pin_is_appended_as_candidate_without_overwriting_approved_pin(tmp_path: Path) -> None:
    manifest, profile = _research_evidence(tmp_path / "package")
    approved = _entry(manifest, profile)
    store = _Store()
    service = ScientificSkillCatalogService(store)
    first = _catalog("v1", (approved,))
    service.publish(first, bindings={approved.entry_id: (manifest, profile)})

    next_manifest = replace(manifest, upstream_pin="fedcba9876543210fedcba9876543210fedcba98", sha256="b" * 64)
    next_profile = replace(profile, skill_sha256="b" * 64, profile_digest="c" * 64)
    candidate = _entry(
        next_manifest,
        next_profile,
        status=ScientificSkillCatalogEntryStatus.CANDIDATE,
        receipt=None,
    )
    second = _catalog("v2", (approved, candidate))
    service.publish(
        second,
        bindings={approved.entry_id: (manifest, profile), candidate.entry_id: (next_manifest, next_profile)},
    )
    assert service.resolve(second, skill_name="literature") == approved

    overwrite = _catalog("v3", (candidate,))
    with pytest.raises(ScientificSkillCatalogError, match="approved_pin_overwrite_denied"):
        service.publish(overwrite, bindings={candidate.entry_id: (next_manifest, next_profile)})


def test_mode_specific_policy_rejects_execution_tools_in_research_entry(tmp_path: Path) -> None:
    manifest, profile = _research_evidence(tmp_path / "package")
    entry = replace(_entry(manifest, profile), allowed_tools=("sandbox_task_request",))
    with pytest.raises(ScientificSkillCatalogError, match="research_policy_invalid"):
        ScientificSkillCatalogService.validate_binding(entry, manifest=manifest, profile=profile)
