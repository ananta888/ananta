"""Current Hub asset policy and image hydration; never an output/render loop."""

import hashlib
import json
import time

from agent.services.meet_contract import MeetError
from agent.services.project_access_authority import ProjectAccessError
from agent.services.source_control_access_policy import HubSourcePrincipal
from ananta_contracts.meet_avatar_image import RESPONSE_SCHEMA, validate_image_binding, validate_image_request


class MeetDialogAvatarImages:
    def __init__(self, authority, meet, profiles, *, clock=time.time):
        self.authority, self.meet, self.profiles, self.clock = authority, meet, profiles, clock

    def projection(self, scope, state):
        selection = scope.avatar_selection
        if selection is None:
            return None  # Preserve the exact legacy dialog envelope.
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
        lease = state["lease"]
        if state["roomId"] != scope.room_id or self.clock() * 1000 >= min(scope.deadline * 1000, lease["expiresAt"]):
            raise MeetError("meet_dialog_avatar_meeting_changed", 403)
        result = {
            name: getattr(scope, name)
            for name in ("tenant_id", "project_id", "task_id", "lease_id", "runtime_id", "session_id", "room_id")
        }
        return validate_image_binding(
            result
            | {
                "meet_session_id": lease["sessionId"],
                "own_peer_id": state["peerId"],
                "generation": lease["generation"],
                "membership_epoch": state["membershipEpoch"],
                "avatar_revision": scope.controls.avatar.revision,
                "deadline_ms": min(scope.deadline * 1000, lease["expiresAt"]),
                "selection_digest": hashlib.sha256(
                    json.dumps(scope.avatar_selection, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            }
        )

    @staticmethod
    def _principal(scope):
        return HubSourcePrincipal(scope.owner_subject, scope.tenant_id, scope.project_id, frozenset({"user"}))
