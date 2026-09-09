"""Read-only Hub generation lease: exact task, current membership and reserved run."""

import time

from agent.models.persona_asset_policy import PersonaSourcePin
from agent.models.persona_generation import PersonaGenerationRequest, admission_digest
from agent.services.source_control_access_policy import HubSourcePrincipal
from ananta_contracts.persona_generation import recipe_digest, validate_assignment


class HubPersonaGenerationLeases:
    def __init__(self, *, state, policy, registry, clock=time.time):
        self.state, self.policy, self.registry, self.clock = state, policy, registry, clock

    def require(self, assignment):
        validate_assignment(assignment, self.clock())
        task = self.state.get(assignment["task_id"])
        if (
            task is None
            or task.status != "in_progress"
            or task.task_kind != "persona_media_generation"
            or (task.tenant_id, task.project_id) != (assignment["tenant_id"], assignment["project_id"])
            or task.worker_execution_context != {"persona_generation": assignment}
        ):
            raise PermissionError("persona_generation_lease_revoked")
        request = PersonaGenerationRequest.model_validate((task.verification_spec or {}).get("persona_generation"))
        pin = PersonaSourcePin.model_validate(task.verification_spec.get("recipe_source"))
        if (
            admission_digest(request, pin) != assignment["admission_digest"]
            or recipe_digest(request.recipe.model_dump(mode="json")) != assignment["source_sha256"]
        ):
            raise PermissionError("persona_generation_request_changed")
        principal = HubSourcePrincipal(
            assignment["owner_subject"], task.tenant_id, task.project_id, frozenset({"user"})
        )
        self.policy.require(principal, task.project_id, request)
        source = self.registry.require_source_identity(
            tenant_id=task.tenant_id,
            project_id=task.project_id,
            source_id=pin.source_id,
            expected_binding_digest=pin.binding_digest,
        )
        if source.origin_type != "persona_generation_recipe" or source.content_digest != assignment["source_sha256"]:
            raise PermissionError("persona_generation_recipe_source_changed")
        evidence = self.registry.assignment_projection(
            tenant_id=task.tenant_id,
            project_id=task.project_id,
            run_id=assignment["run_id"],
            task_id=task.id,
            assignment_id=assignment["assignment_id"],
            dispatch_lease_id=assignment["lease_id"],
        )
        if evidence != assignment["evidence"] or evidence["source_ids"] != sorted(
            [pin.source_id, request.license.source_id]
        ):
            raise PermissionError("persona_generation_evidence_changed")
