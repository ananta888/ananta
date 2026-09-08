"""Read-only parent identity candidate; actual admission must revalidate authority."""

from agent.models.meet_machine_principal import principal_from_role_facts
from agent.services.meet_contract import MeetError


class MeetOrganizationPrincipalPreflight:
    def __init__(self, binding, lifecycle, roles, *, publisher_url, issuer, publishers=None):
        self.binding, self.lifecycle, self.roles = binding, lifecycle, roles
        self.publisher_url, self.issuer = publisher_url, issuer
        self.publishers = publishers

    def inspect(self, principal, project, parent_id):
        if set(principal.roles) & {"worker", "service"} or principal.project_id and principal.project_id != project:
            raise MeetError("meet_dialog_owner_required", 403)
        if not parent_id:
            raise MeetError("meet_dialog_parent_inactive", 403)
        self.binding.require_write_access(principal, project, parent_id)
        fields = self.lifecycle.scope_for_parent(principal.tenant_id, project, parent_id)
        if not fields.get("organization_id"):
            raise MeetError("meet_dialog_organization_principal_unavailable", 409)
        publisher = (
            self.publisher_url
            if self.publishers is None
            else self.publishers.select(principal.tenant_id, project, fields)
        )
        resolved = self.roles.resolve(principal.tenant_id, project, fields, publisher)
        if resolved is None:
            raise MeetError("meet_dialog_principal_assignment_required", 403)
        try:
            candidate = principal_from_role_facts(*resolved)
        except ValueError:
            raise MeetError("meet_dialog_principal_invalid", 403) from None
        return {
            "schema": "ananta.meet-machine-principal-preflight.v1",
            "preflight_only": True,
            "issuer": self.issuer,
            "parent_task_id": parent_id,
            "principal": candidate.projection(),
        }
