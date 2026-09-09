"""Current explicit management authority, shared by generation and receipt ingress."""

from agent.services.project_access_authority import ProjectCapability


class PersonaManagementAuthority:
    def __init__(self, access):
        self.access = access

    def require(self, principal, project):
        if (
            principal.roles & {"worker", "service"}
            or not principal.tenant_id
            or not principal.subject_id
            or (principal.project_id and principal.project_id != project)
        ):
            raise PermissionError("persona_generation_authority_denied")
        self.access.require(
            tenant_id=principal.tenant_id,
            project_id=project,
            subject_id=principal.subject_id,
            capability=ProjectCapability.MANAGE,
            tenant_admin=principal.is_admin,
        )
