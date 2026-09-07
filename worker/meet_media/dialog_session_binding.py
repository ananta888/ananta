"""Compare current browser and Hub projections without logging identity or grants."""


def _lease_reason(local, expected):
    if not isinstance(local, dict) or not isinstance(expected, dict):
        return "lease_shape_changed"
    for field, reason in (
        ("sessionId", "lease_identity_changed"),
        ("generation", "lease_generation_changed"),
        ("expiresAt", "lease_expiry_changed"),
        ("absoluteExpiresAt", "lease_lifetime_changed"),
    ):
        if local.get(field) != expected.get(field):
            return reason
    return "lease_shape_changed"


def require_dialog_session(local, receipt, controls, *, room_id, previous_revision):
    reason = None
    if not local["joined"]:
        reason = "membership_lost"
    elif local["lease"] != receipt["lease"]:
        reason = _lease_reason(local["lease"], receipt["lease"])
    elif receipt["roomId"] != room_id:
        reason = "room_changed"
    elif controls["revision"] < previous_revision:
        reason = "control_regressed"
    if reason is not None:
        # Preserve the existing error family; append only one fixed reason code.
        raise ValueError("meet_dialog_session_changed meet_dialog_" + reason)
