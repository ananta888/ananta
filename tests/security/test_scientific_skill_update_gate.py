from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.scientific_skill_catalog_service import (
    ScientificSkillApprovalLevel,
    ScientificSkillCatalogEntry,
    ScientificSkillCatalogEntryStatus,
    ScientificSkillNetworkProfile,
)
from agent.services.scientific_skill_manifest_service import ScientificSkillFileMetadata, ScientificSkillManifest
from agent.services.scientific_skill_risk_profile_service import (
    ScientificSkillDependency,
    ScientificSkillOperatingMode,
    ScientificSkillRiskProfile,
)
from agent.services.scientific_skill_update_gate_service import ScientificSkillUpdateGateService


def _manifest(pin="0123456789abcdef", sha="a" * 64, files=()):
    return ScientificSkillManifest(
        name="literature", description="Research", upstream_path="skills/literature/SKILL.md",
        upstream_pin=pin, sha256=sha, license="MIT", declared_files=files,
        declared_capabilities=(), declared_dependencies=(), declared_data_classification="internal",
        source_references=(), parser_warnings=(),
    )


def _profile(manifest, **changes):
    values = dict(
        profile_version="ananta.scientific-skill-risk.v1", skill_name=manifest.name,
        skill_sha256=manifest.sha256, operating_mode=ScientificSkillOperatingMode.DOCUMENTATION_ONLY,
        detected_capabilities=(), dependencies=(), network_targets=(), credential_requirements=(),
        data_classification="internal", context_budget_tokens=100, reason_codes=(), manual_assessment_id=None,
        profile_digest="b" * 64,
    )
    values.update(changes)
    return ScientificSkillRiskProfile(**values)


def _entry(manifest, profile):
    return ScientificSkillCatalogEntry.create(
        skill_name=manifest.name, upstream_path=manifest.upstream_path, upstream_pin=manifest.upstream_pin,
        skill_sha256=manifest.sha256, risk_profile_digest=profile.profile_digest,
        status=ScientificSkillCatalogEntryStatus.APPROVED,
        allowed_mode=ScientificSkillOperatingMode.DOCUMENTATION_ONLY,
        context_budget_tokens=100, allowed_tools=(), data_classification="internal",
        network_profile=ScientificSkillNetworkProfile.DENIED,
        allowed_network_targets=(),
        approval_level=ScientificSkillApprovalLevel.NONE,
        approval_receipt_digest="c" * 64,
    )


def test_visible_safe_diff_allows_proposal_but_never_auto_adopts():
    old_file = ScientificSkillFileMetadata("skills/literature/SKILL.md", "1" * 64, 10, "documentation", "markdown")
    new_file = replace(old_file, sha256="2" * 64)
    current = _manifest(files=(old_file,))
    current_profile = _profile(current)
    candidate = _manifest(pin="fedcba9876543210", sha="d" * 64, files=(new_file,))
    candidate_profile = _profile(candidate, profile_digest="e" * 64)
    report = ScientificSkillUpdateGateService().evaluate(
        approved_entry=_entry(current, current_profile), current_manifest=current, current_profile=current_profile,
        candidate_manifest=candidate, candidate_profile=candidate_profile,
    )
    assert report.decision == "proposal_allowed"
    assert report.visible_diff.modified_files == ("skills/literature/SKILL.md",)
    assert report.retained_approved_pin == current.upstream_pin
    assert report.automatic_adoption_performed is False


def test_new_scripts_network_credentials_dependencies_and_rights_block_update():
    current = _manifest()
    current_profile = _profile(current)
    script = ScientificSkillFileMetadata("skills/literature/run.py", "3" * 64, 10, "script", "python")
    candidate = _manifest(pin="fedcba9876543210", sha="d" * 64, files=(script,))
    candidate_profile = _profile(
        candidate, profile_digest="e" * 64,
        operating_mode=ScientificSkillOperatingMode.CONTROLLED_EXECUTION,
        detected_capabilities=("network",), dependencies=(ScientificSkillDependency("python", "requests"),),
        network_targets=("papers.example.test",), credential_requirements=("PAPERS_API_KEY",),
    )
    report = ScientificSkillUpdateGateService().evaluate(
        approved_entry=_entry(current, current_profile), current_manifest=current, current_profile=current_profile,
        candidate_manifest=candidate, candidate_profile=candidate_profile,
    )
    assert report.decision == "blocked"
    assert set(report.blockers) == {
        "scientific_skill_update_capability_expansion", "scientific_skill_update_credential_expansion",
        "scientific_skill_update_dependency_expansion", "scientific_skill_update_network_expansion",
        "scientific_skill_update_new_script", "scientific_skill_update_rights_expansion",
    }
    assert report.retained_approved_pin == current.upstream_pin
    assert report.automatic_adoption_performed is False


def test_candidate_catalog_entry_cannot_be_used_as_approved_update_base():
    current = _manifest()
    current_profile = _profile(current)
    candidate = _manifest(pin="fedcba9876543210", sha="d" * 64)
    candidate_profile = _profile(candidate, profile_digest="e" * 64)
    unapproved_entry = replace(
        _entry(current, current_profile),
        status=ScientificSkillCatalogEntryStatus.CANDIDATE,
    )

    with pytest.raises(ValueError, match="scientific_skill_update_binding_invalid"):
        ScientificSkillUpdateGateService().evaluate(
            approved_entry=unapproved_entry,
            current_manifest=current,
            current_profile=current_profile,
            candidate_manifest=candidate,
            candidate_profile=candidate_profile,
        )
