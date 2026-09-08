"""Hydrate only the current admitted silent clip, under exact Hub/Meet authority."""

import time

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_avatar_binding import avatar_binding, avatar_principal
from agent.services.project_access_authority import ProjectAccessError
from ananta_contracts.meet_avatar_image import validate_image_binding
from ananta_contracts.meet_avatar_video import RESPONSE_SCHEMA, validate_video_request


class MeetDialogAvatarVideos:
    def __init__(self, authority, meet, profiles, *, clock=time.time):
        self.authority, self.meet, self.profiles, self.clock = authority, meet, profiles, clock

    def projection(self, scope, state):
        selection = scope.avatar_selection
        if not scope.avatar_videos or not selection or selection["mode"] != "persona-video-v1":
            raise MeetError("meet_dialog_avatar_videos_not_selected", 403)
        result = {
            "mode": selection["mode"],
            "state": "paused",
            "binding": None,
            "reference": None,
            "repeat_mode": selection["repeat_mode"],
        }
        if scope.controls.avatar is None or not scope.controls.avatar.enabled:
            return result
        try:
            if self.profiles is None or "avatar.publish" not in scope.capabilities:
                raise MeetError("meet_dialog_avatar_video_profiles_unavailable", 409)
            self.profiles.require_current(
                avatar_principal(scope), scope.project_id, selection["profile"], selection["reference"], "publish"
            )
            binding = avatar_binding(scope, state, self.clock())
        except (ValueError, PermissionError, ProjectAccessError):
            return result | {"state": "blocked"}
        return result | {"state": "ready", "binding": binding, "reference": dict(selection["reference"])}

    def hydrate(self, payload):
        validate_video_request(payload, self.clock())
        scope = self._current(payload["binding"])
        selection = scope.avatar_selection
        video, pin = self.profiles.prepare(
            avatar_principal(scope),
            scope.project_id,
            selection["profile"],
            "publish",
            repeat_mode=selection["repeat_mode"],
        )
        if (
            video["reference"] != selection["reference"]
            or pin != selection["profile"]
            or video["repeat_mode"] != selection["repeat_mode"]
        ):
            raise MeetError("meet_dialog_avatar_video_changed", 403)
        self._current(payload["binding"])
        return {"schema": RESPONSE_SCHEMA, "nonce": payload["nonce"], "binding": payload["binding"], "video": video}

    def _current(self, binding):
        binding = validate_image_binding(binding)
        ids = tuple(binding[name] for name in ("task_id", "lease_id", "runtime_id"))
        self.authority.current(*ids)
        state = self.meet.inspect(*ids, binding["meet_session_id"])
        scope = self.authority.current(*ids)
        projection = self.projection(scope, state)
        if projection["state"] != "ready" or projection["binding"] != binding:
            raise MeetError("meet_dialog_avatar_video_revoked_or_changed", 403)
        return scope
