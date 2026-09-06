"""Read-only live authority for one Hub-delegated image inspection."""

import time

from agent.models.persona_assets import PersonaAssetAdmission
from agent.services.persona_inspection_tasks import admission_digest, task_context
from agent.services.source_control_access_policy import HubSourcePrincipal


class HubPersonaInspectionLeases:
    def __init__(self, *, state, policy, registry, clock=time.time):
        self.state, self.policy, self.registry, self.clock = state, policy, registry, clock

    def require(self, assignment):
        task = self.state.get(assignment["task_id"])
        if (
            task is None
            or task.status != "in_progress"
            or task.task_kind != "persona_image_inspection"
            or (task.tenant_id, task.project_id) != (assignment["tenant_id"], assignment["project_id"])
            or task.worker_execution_context != {"persona_image": task_context(assignment)}
            or self.clock() >= assignment["deadline"]
        ):
            raise PermissionError("persona_inspection_lease_revoked")
        admission = PersonaAssetAdmission.model_validate(task.verification_spec.get("persona_admission"))
        if admission_digest(admission) != assignment["admission_digest"] or (
            admission.tenant_id,
            admission.project_id,
            admission.source_sha256,
        ) != (assignment["tenant_id"], assignment["project_id"], assignment["source_sha256"]):
            raise PermissionError("persona_inspection_admission_changed")
        # A background callback never inherits an administrator override. The
        # initiating subject must still have explicit current project membership.
        principal = HubSourcePrincipal(
            assignment["owner_subject"], task.tenant_id, task.project_id, frozenset({"user"})
        )
        self.policy.require_current(principal, admission, "inspect")
        evidence = self.registry.assignment_projection(
            tenant_id=task.tenant_id,
            project_id=task.project_id,
            run_id=assignment["run_id"],
            task_id=task.id,
            assignment_id=assignment["assignment_id"],
            dispatch_lease_id=assignment["lease_id"],
        )
        if evidence != assignment["evidence"]:
            raise PermissionError("persona_inspection_assignment_changed")
