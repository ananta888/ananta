"""Real Hub Task CAS and source fencing with explicitly synthetic asset ports."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_avatar_video import REQUEST_SCHEMA, decode_video_response
from ananta_contracts.meet_dialog import validate_assignment
from tests.test_meet_avatar_video_contract import video_assignment
from tests.test_meet_dialog_avatar_negotiation import system
from tests.test_meet_dialog_avatar_selection import PIN

pytestmark = pytest.mark.timeout(45)


def started(*, videos=True):
    f = system()
    profile = Mock()
    video = video_assignment()
    video["reference"] |= {"tenant_id": "tenant", "project_id": "project"}
    profile.select.return_value = video["reference"], deepcopy(PIN)
    profile.prepare.side_effect = lambda *args, repeat_mode: (
        deepcopy(video) | {"repeat_mode": repeat_mode},
        deepcopy(PIN),
    )
    f.service.avatar_videos.profiles = f.service.avatar_video_selections.profiles = profile
    payload = f.payload | {"avatar_images": True} | ({"avatar_videos": True} if videos else {})
    result = f.service.start(f.principal, "project", payload)
    f.task_id, f.profile, f.video = result["task_id"], profile, video
    f.wire = f.worker.start_dialog.call_args.args[0]
    f.state = {
        "lease": {"sessionId": "ms_" + "a" * 32, "generation": 1, "expiresAt": (f.f.now + 120) * 1000},
        "peerId": "machine",
        "roomId": f.f.context["room_id"],
        "membershipEpoch": 2,
    }
    f.meet.inspect.side_effect = lambda *_: deepcopy(f.state)
    f.callback = {k: f.wire[k] for k in ("task_id", "lease_id", "runtime_id")}
    f.callback |= {"nonce": "a" * 32, "meet_session_id": f.state["lease"]["sessionId"]}
    return f


def select(f, revision=1, repeat="loop"):
    return f.service.select_avatar_video(
        f.principal, "project", f.task_id, {"expected_revision": revision, "profile": PIN, "repeat_mode": repeat}
    )


def activate(f):
    select(f)
    return f.service.control(
        f.principal,
        "project",
        f.task_id,
        {"expected_revision": 2, "chat": False, "audio": False, "screen": False, "avatar": True},
    )


def hydration(f):
    projected = f.service.exchange(f.callback)["avatar"]
    return {"schema": REQUEST_SCHEMA, "nonce": "a" * 32, "sent_at": f.f.now, "binding": projected["binding"]}


def test_negotiated_start_and_video_cas_preserve_pause_legacy_wires_and_other_controls(app):
    with app.app_context():
        f = started()
        assert validate_assignment(f.wire, f.f.now) == f.wire and f.wire["avatar_videos"] is True
        f.profile.select.assert_not_called()
        f.profile.prepare.assert_not_called()
        before = f.service.inspect(f.principal, "project", f.task_id)
        result = select(f)
        assert result["avatar_selection"] == {
            "mode": "persona-video-v1",
            "reference": f.video["reference"],
            "profile": PIN,
            "repeat_mode": "loop",
        }
        assert result["controls"]["avatar"]["enabled"] is False
        assert result["controls"]["avatar"]["revision"] == 2
        assert all(result["controls"][k] == before["controls"][k] for k in ("chat", "audio", "screen"))
        f.profile.prepare.assert_not_called()
        with pytest.raises(MeetError, match="conflict"):
            select(f)
        legacy = started(videos=False)
        assert "avatar_videos" not in legacy.wire
        with pytest.raises(MeetError, match="not_negotiated"):
            select(legacy)
        legacy.profile.select.assert_not_called()


@pytest.mark.parametrize("flag", [False, 1, None, "true"])
def test_malformed_negotiation_denies_before_task_dispatch(app, flag):
    with app.app_context():
        f = system()
        with pytest.raises(MeetError):
            f.service.start(f.principal, "project", f.payload | {"avatar_images": True, "avatar_videos": flag})
        f.worker.start_dialog.assert_not_called()


def test_video_negotiation_needs_images_and_available_profile_authority(app):
    with app.app_context():
        f = system()
        for options in ({"avatar_videos": True}, {"avatar_images": True, "avatar_videos": True}):
            with pytest.raises(MeetError):
                f.service.start(f.principal, "project", f.payload | options)
        f.worker.start_dialog.assert_not_called()


def test_signed_hydration_binding_cannot_use_image_endpoint_and_repeat_is_hub_owned(app):
    with app.app_context():
        f = started()
        activate(f)
        request = hydration(f)
        result = f.service.avatar_video(request)
        assert decode_video_response(result, request, f.video["reference"], "loop", f.f.now * 1000) == f.video
        with pytest.raises(ValueError):
            f.service.avatar_image(request | {"schema": "ananta.meet-avatar-image-request.v1"})
        select(f, revision=3, repeat="hold_last")
        with pytest.raises(MeetError):
            f.service.avatar_video(request)
        newer = hydration(f)
        assert f.service.avatar_video(newer)["video"]["repeat_mode"] == "hold_last"
        f.service.select_avatar(f.principal, "project", f.task_id, {"expected_revision": 4, "neutral": True})
        with pytest.raises(MeetError):
            f.service.avatar_video(newer)


@pytest.mark.parametrize("change", ["cancel", "pause", "profile", "selection", "epoch", "generation", "peer", "expiry"])
def test_revocation_during_video_hydration_never_releases_stale_bytes(app, change):
    with app.app_context():
        f = started()
        activate(f)
        request = hydration(f)

        def prepare(*_, **__):
            if change == "cancel":
                f.tasks.finish_bound(f.task_id, f.wire["lease_id"], f.wire["runtime_id"], "cancelled")
            elif change == "pause":
                f.service.control(
                    f.principal,
                    "project",
                    f.task_id,
                    {"expected_revision": 3, "chat": False, "audio": False, "screen": False, "avatar": False},
                )
            elif change == "profile":
                f.profile.require_current.side_effect = PermissionError("revoked")
            elif change == "selection":
                select(f, revision=3, repeat="hold_last")
            elif change == "epoch":
                f.state["membershipEpoch"] += 1
            elif change == "generation":
                f.state["lease"]["generation"] += 1
            elif change == "peer":
                f.state["peerId"] = "other"
            else:
                f.state["lease"]["expiresAt"] = f.f.now * 1000
            return deepcopy(f.video), deepcopy(PIN)

        f.profile.prepare.side_effect = prepare
        with pytest.raises(MeetError):
            f.service.avatar_video(request)
        f.profile.prepare.assert_called_once()


def test_missing_video_provider_blocks_only_selected_avatar_without_fallback(app):
    with app.app_context():
        f = started()
        active = activate(f)
        f.service.avatar_videos.profiles = None
        result = f.service.exchange(f.callback)
        assert result["avatar"] == {
            "mode": "persona-video-v1",
            "state": "blocked",
            "binding": None,
            "reference": None,
            "repeat_mode": "loop",
        }
        assert result["controls"] == active["controls"]
        assert f.tasks.get_by_id(f.task_id).status == "in_progress"
