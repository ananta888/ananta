"""Hub-owned avatar metadata CAS. Selection never activates a paused source."""

import time

from agent.models.meet_avatar_selection import parse_avatar_selection
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_avatar_controls import advance_avatar_selection_controls


class MeetDialogAvatarSelection:
    def __init__(self, authority, tasks, profiles, *, clock=time.time):
        self.authority, self.tasks, self.profiles, self.clock = authority, tasks, profiles, clock

    def select(self, principal, scope, payload):
        if (
            scope.owner_subject != principal.subject_id
            or scope.tenant_id != principal.tenant_id
            or principal.project_id
            and principal.project_id != scope.project_id
            or set(principal.roles) & {"worker", "service"}
        ):
            raise MeetError("meet_dialog_avatar_owner_required", 403)
        self._current(scope)
        if (
            scope.avatar_selection is None
            or scope.controls.avatar is None
            or "avatar.publish" not in scope.capabilities
        ):
            raise MeetError("meet_dialog_avatar_images_not_negotiated", 409)
        if (
            not isinstance(payload, dict)
            or set(payload) not in ({"expected_revision", "profile"}, {"expected_revision", "neutral"})
            or type(payload["expected_revision"]) is not int
            or "neutral" in payload
            and payload["neutral"] is not True
        ):
            raise MeetError("meet_dialog_avatar_selection_invalid")
        if payload["expected_revision"] != scope.controls.revision or scope.controls.revision >= 1023:
            raise MeetError("meet_dialog_controls_conflict", 409)
        selection = {"mode": "neutral-ai-v1"}
        if "profile" in payload:
            if self.profiles is None:
                raise MeetError("meet_dialog_avatar_profiles_unavailable", 409)
            reference, binding = self.profiles.select(principal, scope.project_id, payload["profile"], "publish")
            selection = {"mode": "persona-image-v1", "reference": reference, "profile": binding}
        selection = parse_avatar_selection(selection, scope.tenant_id, scope.project_id)
        self._current(scope)
        # Even selecting the same image explicitly fences the old generation.
        # Independent controls (including enabled state) are not implicitly changed.
        controls = advance_avatar_selection_controls(scope, int(self.clock() * 1000))
        if not self.tasks.set_avatar_selection(scope, selection, controls):
            raise MeetError("meet_dialog_controls_conflict", 409)
        return selection

    def _current(self, scope):
        current = self.authority.current(scope.task_id, scope.lease_id, scope.runtime_id)
        if current != scope:
            raise MeetError("meet_dialog_controls_conflict", 409)
