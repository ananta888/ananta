"""Exact rejection semantics with content-free mismatch reasons."""

import copy

import pytest

from worker.meet_media.dialog_session_binding import require_dialog_session


@pytest.mark.parametrize(
    "field,reason",
    [
        ("sessionId", "lease_identity_changed"),
        ("generation", "lease_generation_changed"),
        ("expiresAt", "lease_expiry_changed"),
        ("absoluteExpiresAt", "lease_lifetime_changed"),
        ("extra", "lease_shape_changed"),
        ("joined", "membership_lost"),
        ("room", "room_changed"),
        ("revision", "control_regressed"),
        ("lease", "lease_shape_changed"),
    ],
)
def test_existing_session_guard_still_rejects_each_mismatch_without_disclosing_values(field, reason):
    lease = {"sessionId": "PRIVATE_SESSION", "generation": 3, "expiresAt": 123456, "absoluteExpiresAt": 234567}
    local = {"joined": True, "lease": copy.deepcopy(lease)}
    receipt, controls = {"lease": lease, "roomId": "PRIVATE_ROOM"}, {"revision": 2}
    require_dialog_session(local, receipt, controls, room_id="PRIVATE_ROOM", previous_revision=2)
    if field == "joined":
        local["joined"] = False
    elif field == "room":
        receipt["roomId"] = "PRIVATE_FOREIGN_ROOM"
    elif field == "revision":
        controls["revision"] = 1
    elif field == "lease":
        local["lease"] = None
    else:
        local["lease"][field] = "PRIVATE_DIFFERENCE"
    with pytest.raises(ValueError) as error:
        require_dialog_session(local, receipt, controls, room_id="PRIVATE_ROOM", previous_revision=2)
    assert str(error.value) == "meet_dialog_session_changed meet_dialog_" + reason
    assert "PRIVATE" not in str(error.value)
