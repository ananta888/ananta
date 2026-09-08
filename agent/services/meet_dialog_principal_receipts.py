"""Narrow read-only Hub identity mapping; never a grant issuer or provisioning write."""

from agent.services.meet_contract import MeetError


class MeetDialogPrincipalReceipts:
    def __init__(self, authority, tasks, issuer):
        self.authority, self.tasks, self.issuer = authority, tasks, issuer

    def inspect(self, principal, project, task_id):
        if set(principal.roles) & {"worker", "service"} or principal.project_id and principal.project_id != project:
            raise MeetError("meet_dialog_owner_required", 403)
        self.authority.binding.require_write_access(principal, project)
        task = self.tasks.get_by_id(task_id)
        if (
            task is None
            or task.task_kind != "meet_dialog_session"
            or task.tenant_id != principal.tenant_id
            or task.project_id != project
        ):
            raise MeetError("meet_dialog_not_found", 404)
        context = (task.worker_execution_context or {}).get("meet_dialog", {})
        if context.get("owner_subject") != principal.subject_id:
            raise MeetError("meet_dialog_owner_required", 403)
        scope = self.authority.current(task_id, context.get("lease_id"), context.get("runtime_id"))
        if (
            scope.tenant_id != principal.tenant_id
            or scope.project_id != project
            or scope.owner_subject != principal.subject_id
        ):
            raise MeetError("meet_dialog_owner_required", 403)
        if scope.machine_principal is None:
            raise MeetError("meet_dialog_organization_principal_unavailable", 409)
        return {
            "schema": "ananta.meet-machine-principal-receipt.v1",
            "issuer": self.issuer,
            "task_id": task_id,
            "principal": scope.machine_principal.projection(),
        }
