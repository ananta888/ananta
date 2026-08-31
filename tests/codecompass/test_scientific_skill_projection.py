from __future__ import annotations

import json
from pathlib import Path

from agent.services.codecompass_scientific_skill_projection_service import CodeCompassScientificSkillProjectionService
from agent.services.scientific_skill_catalog_service import (
    ScientificSkillApprovalLevel,
    ScientificSkillCatalog,
    ScientificSkillCatalogEntry,
    ScientificSkillCatalogEntryStatus,
    ScientificSkillNetworkProfile,
)
from agent.services.scientific_skill_manifest_service import ScientificSkillManifestImporter
from agent.services.scientific_skill_risk_profile_service import ScientificSkillOperatingMode, ScientificSkillRiskProfiler


def _bound_skill(root: Path):
    skill = root / "skills" / "literature"
    skill.mkdir(parents=True)
    (root / "plugin.json").write_text(json.dumps({"name": "research", "license": "MIT"}), encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: literature\ndescription: Evidence review\n---\nRead citations only.\n",
        encoding="utf-8",
    )
    manifest = ScientificSkillManifestImporter().inspect(
        package_path=root,
        upstream_repository="https://github.com/K-Dense-AI/scientific-agent-skills",
        upstream_pin="0123456789abcdef",
    ).skills[0]
    contents = {item.relative_path: (root / item.relative_path).read_bytes() for item in manifest.declared_files}
    profile = ScientificSkillRiskProfiler().profile(manifest=manifest, declared_contents=contents)
    entry = ScientificSkillCatalogEntry.create(
        skill_name=manifest.name, upstream_path=manifest.upstream_path, upstream_pin=manifest.upstream_pin,
        skill_sha256=manifest.sha256, risk_profile_digest=profile.profile_digest,
        status=ScientificSkillCatalogEntryStatus.APPROVED, allowed_mode=ScientificSkillOperatingMode.DOCUMENTATION_ONLY,
        context_budget_tokens=profile.context_budget_tokens, allowed_tools=(), data_classification=profile.data_classification,
        network_profile=ScientificSkillNetworkProfile.DENIED, allowed_network_targets=(),
        approval_level=ScientificSkillApprovalLevel.NONE, approval_receipt_digest="d" * 64,
    )
    catalog = ScientificSkillCatalog.create(catalog_id="scientific", catalog_version="v1", feature_enabled=True, entries=(entry,))
    return manifest, profile, entry, catalog


def test_projection_explains_selection_and_supply_chain_without_reading_skill_content(tmp_path: Path):
    manifest, profile, entry, catalog = _bound_skill(tmp_path / "package")
    projection = CodeCompassScientificSkillProjectionService().project(
        manifest=manifest, profile=profile, catalog=catalog, entry=entry,
        task_id="task-1", source_id="scientific-catalog-source", selection_status="selected",
        selection_reason="approved exact pin", execution_receipt_digests=("e" * 64,),
    )
    serialized = json.dumps([node.__dict__ for node in projection.nodes], sort_keys=True)
    assert "approved exact pin" in projection.explanation
    assert entry.upstream_pin in serialized and entry.skill_sha256 in serialized
    assert {edge.relation for edge in projection.edges} >= {"governed_by", "originates_from", "selected_for"}
    assert "Read citations only" not in serialized
    assert "secret" not in serialized.lower()


def test_rejected_projection_is_immutable_historical_evidence(tmp_path: Path):
    manifest, profile, entry, catalog = _bound_skill(tmp_path / "package")
    service = CodeCompassScientificSkillProjectionService()
    historical = service.project(
        manifest=manifest, profile=profile, catalog=catalog, entry=entry,
        task_id="task-2", source_id="scientific-catalog-source", selection_status="rejected", selection_reason="policy denied",
    )
    disabled_entry = ScientificSkillCatalogEntry.create(
        skill_name=entry.skill_name, upstream_path=entry.upstream_path, upstream_pin=entry.upstream_pin,
        skill_sha256=entry.skill_sha256, risk_profile_digest=entry.risk_profile_digest,
        status=ScientificSkillCatalogEntryStatus.DISABLED, allowed_mode=entry.allowed_mode,
        context_budget_tokens=entry.context_budget_tokens, allowed_tools=entry.allowed_tools,
        data_classification=entry.data_classification, network_profile=entry.network_profile,
        allowed_network_targets=entry.allowed_network_targets, approval_level=entry.approval_level,
        approval_receipt_digest=None,
    )
    assert disabled_entry.status is ScientificSkillCatalogEntryStatus.DISABLED
    assert historical.explanation.endswith("policy denied")
    assert any(edge.relation == "rejected_for" for edge in historical.edges)
