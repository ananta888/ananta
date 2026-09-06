"""Hub-owned association lifecycle; Meet remains the room admission authority."""

import re
from collections.abc import Callable

from agent.services.meet_contract import MeetError, MeetingStore, MeetProfile
from agent.services.project_access_authority import ProjectAccessPort, ProjectCapability
from agent.services.source_control_access_policy import HubSourcePrincipal


class MeetBindingService:
    def __init__(
        self,
        profile: MeetProfile,
        store: MeetingStore,
        access: ProjectAccessPort,
        task_access: Callable[[HubSourcePrincipal, str, str], None],
    ):
        self.profile = profile
        self.store = store
        self.access = access
        self.task_access = task_access

    def _authorize(self, principal, project, task, *, write=False):
        if not principal.tenant_id or not principal.subject_id or len(principal.subject_id) > 255:
            raise MeetError("meet_identity_required", 403)
        if principal.roles & {"worker", "service"}:
            raise MeetError("meet_user_required", 403)
        if principal.project_id and principal.project_id != project:
            raise MeetError("meet_context_mismatch", 403)
        for value in (principal.tenant_id, project):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
                raise MeetError("meet_scope_invalid")
        if task and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", task):
            raise MeetError("meet_task_invalid")
        self.access.require(
            tenant_id=principal.tenant_id,
            project_id=project,
            subject_id=principal.subject_id,
            tenant_admin=principal.is_admin,
            capability=ProjectCapability.WRITE if write else ProjectCapability.READ,
        )
        if task:
            self.task_access(principal, project, task)

    def read(self, principal, project, task=""):
        self._authorize(principal, project, task)
        return self._projection(self.store.get(principal.tenant_id, project, task), project, task)

    def change(self, principal, project, task, payload, *, unlink=False):
        self._authorize(principal, project, task, write=True)
        fields = {"expected_revision"} if unlink else {"expected_revision", "invite_url"}
        if not isinstance(payload, dict) or set(payload) != fields:
            raise MeetError("meet_payload_invalid")
        expected = payload["expected_revision"]
        if type(expected) is not int or not 0 <= expected < 2_147_483_647:
            raise MeetError("meet_revision_invalid")
        room = None if unlink else self.profile.parse_invite(payload["invite_url"])
        current = self.store.get(principal.tenant_id, project, task)
        if current.revision != expected:
            raise MeetError("meet_binding_conflict", 409)
        if current.room_id == room:
            return self._projection(current, project, task)
        updated = self.store.replace(principal.tenant_id, project, task, expected, room, principal.subject_id)
        return self._projection(updated, project, task)

    def _projection(self, binding, project, task):
        return {
            "schema": "ananta.meet-binding.v1",
            "project_id": project,
            "task_id": task or None,
            "revision": binding.revision,
            "invite_url": self.profile.invite(binding.room_id) if binding.room_id else None,
            "room_verified": False,
            "membership_granted": False,
            "profile": self.profile.public(),
        }
