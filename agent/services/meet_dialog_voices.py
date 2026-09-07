"""Independent current voice authority; projection does not change controls or publish."""

import time

from agent.services.meet_contract import MeetError
from agent.services.source_control_access_policy import HubSourcePrincipal
from ananta_contracts.meet_dialog_voice import content_digest, validate_voice_projection
from ananta_contracts.meet_speech import validate_speech_profile


class MeetDialogVoices:
    def __init__(self, profiles, configured_profile, *, clock=time.time):
        self.profiles = profiles
        self.configured_profile = (
            validate_speech_profile(configured_profile) if configured_profile is not None else None
        )
        self.clock = clock

    def projection(self, scope):
        selection = scope.voice_selection
        if selection is None:
            return None
        if scope.controls.speech is None:
            raise MeetError("meet_dialog_voice_controls_missing", 403)
        result = {
            "mode": selection["mode"],
            "state": "paused",
            "speech_revision": scope.controls.speech.revision,
            "selection_digest": content_digest(selection),
            "profile": None,
        }
        if not scope.controls.speech.enabled:
            return validate_voice_projection(result)
        try:
            result = result | {"state": "ready", "profile": self.prepare(scope)}
        except (ValueError, PermissionError):
            result = result | {"state": "blocked"}
        return validate_voice_projection(result)

    def prepare(self, scope):
        if (
            scope.voice_selection is None
            or scope.controls.speech is None
            or not scope.controls.speech.enabled
            or "speech.publish" not in scope.capabilities
            or self.configured_profile is None
            or self.clock() >= scope.deadline
        ):
            raise MeetError("meet_dialog_voice_unavailable", 403)
        selection = scope.voice_selection
        if selection["mode"] == "configured-piper-v1":
            return dict(self.configured_profile)
        if self.profiles is None:
            raise MeetError("meet_dialog_voice_profiles_unavailable", 403)
        principal = HubSourcePrincipal(scope.owner_subject, scope.tenant_id, scope.project_id, frozenset({"user"}))
        value, binding = self.profiles.prepare(
            principal,
            scope.project_id,
            selection["profile"],
            "publish",
            max_seconds=self.configured_profile["max_seconds"],
        )
        if value["reference"] != selection["reference"] or binding != selection["profile"]:
            raise MeetError("meet_dialog_voice_changed", 403)
        return validate_speech_profile(value["speech_profile"])

    def require_current(self, scope, projection):
        expected = validate_voice_projection(projection)
        if expected["state"] != "ready" or self.projection(scope) != expected:
            raise MeetError("meet_dialog_voice_changed", 403)
