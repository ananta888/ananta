"""Content-free source-generation identity shared by image and video hydration."""

import hashlib
import json

from agent.services.meet_contract import MeetError
from agent.services.source_control_access_policy import HubSourcePrincipal
from ananta_contracts.meet_avatar_image import validate_image_binding


def avatar_principal(scope):
    return HubSourcePrincipal(scope.owner_subject, scope.tenant_id, scope.project_id, frozenset({"user"}))


def avatar_binding(scope, state, now):
    lease = state["lease"]
    if state["roomId"] != scope.room_id or now * 1000 >= min(scope.deadline * 1000, lease["expiresAt"]):
        raise MeetError("meet_dialog_avatar_meeting_changed", 403)
    return validate_image_binding(
        {
            **{
                name: getattr(scope, name)
                for name in (
                    "tenant_id",
                    "project_id",
                    "task_id",
                    "lease_id",
                    "runtime_id",
                    "session_id",
                    "room_id",
                )
            },
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
