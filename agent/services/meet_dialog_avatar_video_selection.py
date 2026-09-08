"""Owner-controlled silent clip selection, never source activation or dispatch."""

import time

from agent.models.meet_avatar_selection import parse_avatar_selection
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_avatar_controls import advance_avatar_selection_controls


class MeetDialogAvatarVideoSelection:
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
        if not scope.avatar_videos or scope.avatar_selection is None or scope.controls.avatar is None:
            raise MeetError("meet_dialog_avatar_videos_not_negotiated", 409)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"expected_revision", "profile", "repeat_mode"}
            or type(payload["expected_revision"]) is not int
            or payload["repeat_mode"] not in ("loop", "hold_last")
        ):
            raise MeetError("meet_dialog_avatar_video_selection_invalid")
        if payload["expected_revision"] != scope.controls.revision or scope.controls.revision >= 1023:
            raise MeetError("meet_dialog_controls_conflict", 409)
        if self.profiles is None:
            raise MeetError("meet_dialog_avatar_video_profiles_unavailable", 409)
        reference, binding = self.profiles.select(principal, scope.project_id, payload["profile"], "publish")
        selection = parse_avatar_selection(
            {
                "mode": "persona-video-v1",
                "reference": reference,
                "profile": binding,
                "repeat_mode": payload["repeat_mode"],
            },
            scope.tenant_id,
            scope.project_id,
            videos=True,
        )
        self._current(scope)
        controls = advance_avatar_selection_controls(scope, int(self.clock() * 1000))
        if not self.tasks.set_avatar_selection(scope, selection, controls):
            raise MeetError("meet_dialog_controls_conflict", 409)
        return selection

    def _current(self, scope):
        if self.authority.current(scope.task_id, scope.lease_id, scope.runtime_id) != scope:
            raise MeetError("meet_dialog_controls_conflict", 409)
