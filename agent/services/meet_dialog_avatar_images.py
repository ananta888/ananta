"""Current Hub asset policy and image hydration; never an output/render loop."""

import time

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_avatar_binding import avatar_binding, avatar_principal
from agent.services.project_access_authority import ProjectAccessError
from ananta_contracts.meet_avatar_image import RESPONSE_SCHEMA, validate_image_binding, validate_image_request


class MeetDialogAvatarImages:
    def __init__(self, authority, meet, profiles, *, clock=time.time):
        self.authority, self.meet, self.profiles, self.clock = authority, meet, profiles, clock

    def projection(self, scope, state):
        selection = scope.avatar_selection
        if selection is None:
            return None  # Preserve the exact legacy dialog envelope.
        if selection["mode"] not in ("neutral-ai-v1", "persona-image-v1"):
            raise MeetError("meet_dialog_avatar_image_not_selected", 403)
        result = {"mode": selection["mode"], "state": "paused", "binding": None, "reference": None}
        if scope.controls.avatar is None or not scope.controls.avatar.enabled:
            return result
        if "avatar.publish" not in scope.capabilities:
            return result | {"state": "blocked"}
        if selection["mode"] == "neutral-ai-v1":
            return result | {"state": "ready"}
        try:
            if self.profiles is None:
                raise MeetError("meet_dialog_avatar_profiles_unavailable", 409)
            self.profiles.require_current(
                self._principal(scope), scope.project_id, selection["profile"], selection["reference"], "publish"
            )
            binding = self._binding(scope, state)
        except (ValueError, PermissionError, ProjectAccessError):
            # Denial affects only this source; it never silently chooses neutral
            # or modifies unrelated chat, speech or screen activation.
            return result | {"state": "blocked"}
        return result | {"state": "ready", "binding": binding, "reference": dict(selection["reference"])}

    def hydrate(self, payload):
        validate_image_request(payload, self.clock())
        scope = self._current(payload["binding"])
        selection = scope.avatar_selection
        image, pin = self.profiles.prepare(self._principal(scope), scope.project_id, selection["profile"], "publish")
        if image["reference"] != selection["reference"] or pin != selection["profile"]:
            raise MeetError("meet_dialog_avatar_image_changed", 403)
        self._current(payload["binding"])
        return {"schema": RESPONSE_SCHEMA, "nonce": payload["nonce"], "binding": payload["binding"], "image": image}

    def _current(self, binding):
        binding = validate_image_binding(binding)
        identifiers = tuple(binding[name] for name in ("task_id", "lease_id", "runtime_id"))
        self.authority.current(*identifiers)
        state = self.meet.inspect(*identifiers, binding["meet_session_id"])
        scope = self.authority.current(*identifiers)
        projection = self.projection(scope, state)
        if not projection or projection["state"] != "ready" or projection["binding"] != binding:
            raise MeetError("meet_dialog_avatar_image_revoked_or_changed", 403)
        return scope

    def _binding(self, scope, state):
        return avatar_binding(scope, state, self.clock())

    @staticmethod
    def _principal(scope):
        return avatar_principal(scope)
