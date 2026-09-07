"""Passive voice CAS fences only speech authority and never activates an output."""

import time

from agent.models.meet_voice_selection import parse_voice_selection
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_controls import controls_projection


def advance_voice_selection_controls(scope, since):
    old = scope.controls.speech
    if (
        old is None
        or "speech.publish" not in scope.capabilities
        or scope.controls.revision >= 1023
        or type(since) is not int
        or not old.since <= since < 2**53
    ):
        raise MeetError("meet_dialog_voice_controls_conflict", 409)
    controls = controls_projection(scope.controls)
    controls["revision"] += 1
    controls["speech"] = {"enabled": old.enabled, "revision": old.revision + 1, "since": since}
    return controls


class MeetDialogVoiceSelection:
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
            raise MeetError("meet_dialog_voice_owner_required", 403)
        self._current(scope)
        if scope.voice_selection is None or scope.controls.speech is None or "speech.publish" not in scope.capabilities:
            raise MeetError("meet_dialog_voice_profiles_not_negotiated", 409)
        if (
            not isinstance(payload, dict)
            or set(payload) not in ({"expected_revision", "profile"}, {"expected_revision", "configured"})
            or type(payload["expected_revision"]) is not int
            or "configured" in payload
            and payload["configured"] is not True
        ):
            raise MeetError("meet_dialog_voice_selection_invalid")
        if payload["expected_revision"] != scope.controls.revision or scope.controls.revision >= 1023:
            raise MeetError("meet_dialog_controls_conflict", 409)
        selection = {"mode": "configured-piper-v1"}
        if "profile" in payload:
            if self.profiles is None:
                raise MeetError("meet_dialog_voice_profiles_unavailable", 409)
            reference, binding = self.profiles.select(principal, scope.project_id, payload["profile"], "publish")
            selection = {"mode": "persona-voice-v1", "reference": reference, "profile": binding}
        selection = parse_voice_selection(selection, scope.tenant_id, scope.project_id)
        self._current(scope)
        controls = advance_voice_selection_controls(scope, int(self.clock() * 1000))
        if not self.tasks.set_voice_selection(scope, selection, controls):
            raise MeetError("meet_dialog_controls_conflict", 409)
        return selection

    def _current(self, scope):
        if self.authority.current(scope.task_id, scope.lease_id, scope.runtime_id) != scope:
            raise MeetError("meet_dialog_controls_conflict", 409)
