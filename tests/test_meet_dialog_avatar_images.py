"""Hub-only image hydration rechecks task, profile and actual Meet authority."""

import copy
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_avatar_images import MeetDialogAvatarImages
from ananta_contracts.meet_avatar_image import REQUEST_SCHEMA, decode_image_response
from tests.test_meet_avatar_image_bridge import image_assignment
from tests.test_meet_dialog_avatar_controls import avatar_scope
from tests.test_meet_dialog_avatar_selection import image_selection


def fixture():
    f, _ = avatar_scope()
    f.context["avatar_selection"] = image_selection()
    f.context["controls"]["avatar"]["enabled"] = True
    scope = f.authority.current("task", "dispatch", "runtime")
    state = {
        "lease": {"sessionId": "ms_" + "a" * 32, "generation": 1, "expiresAt": (f.now + 60) * 1000},
        "peerId": "machine",
        "membershipEpoch": 2,
        "roomId": scope.room_id,
    }
    meet, profiles = Mock(), Mock()
    meet.inspect.side_effect = lambda *_: copy.deepcopy(state)
    image = image_assignment() | {"reference": image_selection()["reference"]}
    profiles.prepare.return_value = (image, scope.avatar_selection["profile"])
    service = MeetDialogAvatarImages(f.authority, meet, profiles, clock=lambda: f.now)
    projection = service.projection(scope, state)
    request = {"schema": REQUEST_SCHEMA, "nonce": "a" * 32, "sent_at": f.now, "binding": projection["binding"]}
    return f, scope, state, meet, profiles, service, request, image


def test_ready_projection_is_content_free_and_hydration_rechecks_both_authorities():
    f, scope, state, meet, profiles, service, request, image = fixture()
    projection = service.projection(scope, state)
    assert set(projection) == {"state", "mode", "binding", "reference"}
    assert "profile" not in projection and "png" not in projection and projection["mode"] == "persona-image-v1"
    result = service.hydrate(request)
    assert decode_image_response(result, request, image["reference"], f.now * 1000) == image
    profiles.prepare.assert_called_once()
    assert meet.inspect.call_count == 2
    assert all(
        call.args == ("task", "dispatch", "runtime", state["lease"]["sessionId"])
        for call in meet.inspect.call_args_list
    )


@pytest.mark.parametrize("change", ["profile", "asset", "unconfigured", "paused"])
def test_revoked_or_paused_avatar_does_not_grant_neutral_or_change_other_sources(change):
    f, scope, state, meet, profiles, service, request, image = fixture()
    original = copy.deepcopy(f.context["controls"])
    if change == "unconfigured":
        service.profiles = None
    elif change == "paused":
        f.context["controls"]["avatar"]["enabled"] = False
        scope = f.authority.current("task", "dispatch", "runtime")
    else:
        profiles.require_current.side_effect = PermissionError(change + "_revoked")
    result = service.projection(scope, state)
    assert result == {
        "mode": "persona-image-v1",
        "state": "paused" if change == "paused" else "blocked",
        "binding": None,
        "reference": None,
    }
    with pytest.raises(MeetError, match="revoked_or_changed"):
        service.hydrate(request)
    profiles.prepare.assert_not_called()
    assert all(f.context["controls"][key] == original[key] for key in ("chat", "audio", "speech", "screen"))


@pytest.mark.parametrize(
    "change", ["control", "selection", "cancel", "profile", "membership", "lease", "peer", "room", "expiry"]
)
def test_change_during_image_read_cannot_release_stale_bytes(change):
    f, scope, state, meet, profiles, service, request, image = fixture()

    def prepare(*_):
        if change == "control":
            f.context["controls"]["avatar"]["enabled"] = False
        elif change == "selection":
            f.context["avatar_selection"] = {"mode": "neutral-ai-v1"}
        elif change == "cancel":
            f.task.status = "cancelled"
        elif change == "profile":
            profiles.require_current.side_effect = PermissionError("revoked")
        elif change == "membership":
            state["membershipEpoch"] += 1
        elif change == "lease":
            state["lease"]["generation"] += 1
        elif change == "peer":
            state["peerId"] = "other"
        elif change == "room":
            state["roomId"] = "room-" + "b" * 18
        else:
            state["lease"]["expiresAt"] = f.now * 1000
        return image, scope.avatar_selection["profile"]

    profiles.prepare.side_effect = prepare
    with pytest.raises(MeetError):
        service.hydrate(request)
    profiles.prepare.assert_called_once()


def test_wrong_image_reference_or_profile_receipt_is_not_returned():
    for field in ("image", "profile"):
        f, scope, state, meet, profiles, service, request, image = fixture()
        if field == "image":
            profiles.prepare.return_value = (
                image | {"reference": image["reference"] | {"artifact_id": "other"}},
                scope.avatar_selection["profile"],
            )
        else:
            profiles.prepare.return_value = (image, scope.avatar_selection["profile"] | {"selection_digest": "f" * 64})
        with pytest.raises(MeetError, match="image_changed"):
            service.hydrate(request)


def test_legacy_task_has_no_image_projection_or_authority_and_neutral_never_loads_image():
    f, scope, state, meet, profiles, service, request, image = fixture()
    del f.context["avatar_selection"]
    legacy = f.authority.current("task", "dispatch", "runtime")
    assert service.projection(legacy, state) is None
    with pytest.raises(MeetError):
        service.hydrate(request)
    f.context["avatar_selection"] = {"mode": "neutral-ai-v1"}
    neutral = f.authority.current("task", "dispatch", "runtime")
    profiles.require_current.reset_mock()
    assert service.projection(neutral, state) == {
        "mode": "neutral-ai-v1",
        "state": "ready",
        "binding": None,
        "reference": None,
    }
    profiles.require_current.assert_not_called()
    profiles.prepare.assert_not_called()
