"""Revalidate explicit management and immutable generator license, never infer consent."""

from agent.models.persona_generation import PersonaGenerationRequest
from agent.services.persona_management_authority import PersonaManagementAuthority


class PersonaGenerationPolicy:
    def __init__(self, *, access, registry):
        self.authority, self.registry = PersonaManagementAuthority(access), registry

    def require(self, principal, project, request):
        self.authority.require(principal, project)
        request = PersonaGenerationRequest.model_validate_json(request.model_dump_json())
        proof = self.registry.require_source_identity(
            tenant_id=principal.tenant_id,
            project_id=project,
            source_id=request.license.source_id,
            expected_binding_digest=request.license.binding_digest,
        )
        if proof.origin_type != "license_document":
            raise PermissionError("persona_generation_license_required")
        if request.classification != "test_only" and (proof.synthetic or proof.evidence_scope == "test"):
            raise PermissionError("persona_generation_test_license_not_promotable")
        return request
