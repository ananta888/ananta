from __future__ import annotations

from agent.services.scientific_skill_access_service import ScientificSkillAccessService
from agent.services.scientific_skill_catalog_service import (
    ScientificSkillApprovalLevel, ScientificSkillCatalogEntry, ScientificSkillCatalogEntryStatus, ScientificSkillNetworkProfile,
)
from agent.services.scientific_skill_risk_profile_service import ScientificSkillOperatingMode
from agent.services.source_control_access_policy import HubSourcePrincipal, SourceObjectBinding


def _entry():
    return ScientificSkillCatalogEntry.create(
        skill_name="literature", upstream_path="skills/literature/SKILL.md", upstream_pin="0123456789abcdef",
        skill_sha256="a" * 64, risk_profile_digest="b" * 64, status=ScientificSkillCatalogEntryStatus.APPROVED,
        allowed_mode=ScientificSkillOperatingMode.DOCUMENTATION_ONLY, context_budget_tokens=100, allowed_tools=(),
        data_classification="internal", network_profile=ScientificSkillNetworkProfile.DENIED, allowed_network_targets=(),
        approval_level=ScientificSkillApprovalLevel.NONE, approval_receipt_digest="c" * 64,
    )


def _principal(*roles, tenant="tenant-1", project="project-1"):
    return HubSourcePrincipal("user-1", tenant, project, frozenset(roles))


def test_only_scoped_owner_or_admin_can_view_dependencies_and_credential_requirements():
    binding = SourceObjectBinding("scientific-default", "tenant-1", "project-1")
    service = ScientificSkillAccessService()
    maintainer = service.project(
        principal=_principal("project_maintainer"), binding=binding, entry=_entry(),
        dependencies=("python:requests",), credential_requirements=("secret:literature-api",),
    )
    assert maintainer is not None
    assert maintainer.dependencies is None and maintainer.credential_requirements is None and maintainer.upstream_pin is None
    owner = service.project(
        principal=_principal("project_owner"), binding=binding, entry=_entry(),
        dependencies=("python:requests",), credential_requirements=("secret:literature-api",),
    )
    assert owner is not None
    assert owner.dependencies == ("python:requests",)
    assert owner.credential_requirements == ("secret:literature-api",)


def test_foreign_scope_is_hidden_even_when_sensitive_metadata_is_requested():
    view = ScientificSkillAccessService().project(
        principal=_principal("project_owner", tenant="tenant-2"),
        binding=SourceObjectBinding("scientific-default", "tenant-1", "project-1"), entry=_entry(),
        dependencies=("python:requests",), credential_requirements=("secret:literature-api",),
    )
    assert view is None
