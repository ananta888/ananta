"""Immutable recovery projection from the Hub's validated authority DTO."""

import hashlib
import json
from dataclasses import asdict

from agent.models.meet_dialog_recovery import RecoveryOwner
from agent.services.meet_contract import MeetError


def recovery_owner(scope):
    if scope.reconnect is not True:
        raise MeetError("meet_reconnect_not_negotiated", 409)
    # Bind all current identity/profile ceilings, including the verified role
    # principal. Mutable source controls/selections are rechecked separately;
    # their negotiation presence must remain immutable, not their current asset.
    value = asdict(scope)
    del value["controls"]
    for name in ("avatar_selection", "voice_selection"):
        value[name] = value[name] is not None
    try:
        raw = json.dumps(
            {"schema": "ananta.meet-recovery-binding.v1", "authority": value},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (ValueError, TypeError):
        raise MeetError("meet_recovery_binding_invalid", 409) from None
    if len(raw) > 16384:
        raise MeetError("meet_recovery_binding_invalid", 409)
    return RecoveryOwner(scope.task_id, hashlib.sha256(raw).hexdigest(), scope.deadline * 1000)
