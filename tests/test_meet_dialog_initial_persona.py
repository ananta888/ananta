"""Actual Hub Task admission and dispatch races with explicit synthetic profile ports."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_dialog import validate_assignment
from ananta_contracts.meet_speech import speech_profile
from tests.test_meet_avatar_video_contract import video_assignment
from tests.test_meet_dialog_avatar_negotiation import system
from tests.test_meet_dialog_avatar_selection import PIN, image_selection

pytestmark = pytest.mark.timeout(45)


def fixture():
    f = system()
    f.video = Mock()
    reference = video_assignment()["reference"] | {"tenant_id": "tenant", "project_id": "project"}
    f.video.select.return_value = reference, deepcopy(PIN)
    f.service.avatar_videos.profiles = f.service.avatar_video_selections.profiles = f.video
    f.voice = Mock()
    f.voice.select.return_value = (
        image_selection()["reference"] | {"kind": "voice", "artifact_id": "voice"},
        deepcopy(PIN),
    )
    f.service.voices.profiles = f.service.voice_selections.profiles = f.voice
    f.service.voices.configured_profile = speech_profile()
    f.options = f.payload | {
        "avatar_images": True,
        "avatar_videos": True,
        "voice_profiles": True,
        "capabilities": ["avatar.publish", "speech.publish", "chat.read", "chat.send"],
    }
    return f


def choice(mode="persona-video-v1"):
    return {
        "avatar": {
            "mode": mode,
            "profile": deepcopy(PIN),
            **({"repeat_mode": "hold_last"} if mode == "persona-video-v1" else {}),
        },
        "voice": {"profile": deepcopy(PIN)},
    }


@pytest.mark.parametrize("mode", ["persona-image-v1", "persona-video-v1"])
def test_initial_selection_is_resolved_before_task_and_rechecked_before_dispatch_without_hydration(app, mode):
    with app.app_context():
        f = fixture()
        selected = choice(mode)
        original = f.tasks.start

        def create(*args, **kwargs):
            (f.video if mode == "persona-video-v1" else f.profile_service).select.assert_called_once()
            f.voice.select.assert_called_once()
            return original(*args, **kwargs)

        f.tasks.start = Mock(side_effect=create)
        result = f.service.start(f.principal, "project", f.options | {"initial_persona": selected})
        wire = f.worker.start_dialog.call_args.args[0]
        assert validate_assignment(wire, f.f.now) is wire
        row = f.tasks.get_by_id(result["task_id"])
        stored = row.worker_execution_context["meet_dialog"]
        assert stored["initial_persona"] == wire["initial_persona"]
        assert stored["avatar_selection"]["mode"] == mode and stored["voice_selection"]["mode"] == "persona-voice-v1"
        assert stored["controls"]["avatar"]["enabled"] is False and stored["controls"]["speech"]["enabled"] is False
        assert stored["controls"]["revision"] == 1
        for port in [f.profile_service, f.video, f.voice]:
            port.prepare.assert_not_called()
        assert f.voice.require_current.call_count == 3
        selected["voice"]["profile"]["owner_id"] = "changed"
        assert stored["voice_selection"]["profile"] == PIN


@pytest.mark.parametrize(
    "case", ["unknown", "empty", "null", "profile", "bytes", "repeat", "unnegotiated", "provider", "denied"]
)
def test_denied_initial_choice_never_creates_task_grant_or_worker_dispatch(app, case):
    with app.app_context():
        f = fixture()
        selected, options = choice(), dict(f.options)
        if case == "unknown":
            selected["url"] = "https://foreign"
        elif case == "empty":
            selected = {}
        elif case == "null":
            selected = None
        elif case == "profile":
            selected["avatar"]["profile"]["selection_digest"] = "invalid"
        elif case == "bytes":
            selected["avatar"]["mp4"] = "AAAA"
        elif case == "repeat":
            selected["avatar"]["repeat_mode"] = False
        elif case == "unnegotiated":
            options.pop("avatar_videos")
        elif case == "provider":
            f.service.avatar_videos.profiles = None
        else:
            f.video.select.side_effect = MeetError("synthetic_revoked", 403)
        f.tasks.start = Mock(wraps=f.tasks.start)
        with pytest.raises(MeetError):
            f.service.start(f.principal, "project", options | {"initial_persona": selected})
        f.tasks.start.assert_not_called()
        f.worker.start_dialog.assert_not_called()
        f.issuer.issue_dialog.assert_not_called()


@pytest.mark.parametrize("profile", [None, [], {"owner_id": "private-invalid-profile"}])
def test_malformed_initial_profile_returns_bounded_http_error_without_creating_task(app, monkeypatch, profile):
    import agent.auth as auth
    from tests.test_meet_avatar_image_routes import application

    with app.app_context():
        f = fixture()
        http_app, _ = application()
        http_app.extensions["meet_dialog_service"] = f.service
        monkeypatch.setattr(
            auth,
            "_validate_user_jwt",
            lambda _: {"sub": "owner", "tenant_id": "tenant", "project_id": "project", "role": "user"},
        )
        monkeypatch.setattr(auth, "_user_token_allows_current_request", lambda _: True)
        selected = choice()
        selected["avatar"]["profile"] = profile
        f.tasks.start = Mock(wraps=f.tasks.start)
        result = http_app.test_client().post(
            "/api/meet/v1/projects/project/dialogs",
            json=f.options | {"initial_persona": selected},
            headers={"Authorization": "Bearer synthetic-user"},
        )
        assert result.status_code == 400
        assert result.json == {"error": {"code": "meet_initial_persona_invalid"}}
        assert result.headers["Cache-Control"] == "no-store"
        f.tasks.start.assert_not_called()
        f.worker.start_dialog.assert_not_called()
        f.issuer.issue_dialog.assert_not_called()


@pytest.mark.parametrize("stage", ["after_task", "after_grant"])
def test_revocation_between_creation_and_dispatch_fails_exact_task_and_never_falls_back(app, stage):
    with app.app_context():
        f = fixture()
        if stage == "after_task":
            original = f.tasks.start

            def changed(*args, **kwargs):
                result = original(*args, **kwargs)
                f.video.require_current.side_effect = MeetError("synthetic_revoked", 403)
                return result

            f.tasks.start = changed
        else:
            grant = f.issuer.issue_dialog.return_value

            def changed(*args, **kwargs):
                f.video.require_current.side_effect = MeetError("synthetic_revoked", 403)
                return grant

            f.issuer.issue_dialog.side_effect = changed
        f.tasks.finish_bound = Mock(wraps=f.tasks.finish_bound)
        with pytest.raises(MeetError, match="revoked"):
            f.service.start(f.principal, "project", f.options | {"initial_persona": choice()})
        f.worker.start_dialog.assert_not_called()
        f.tasks.finish_bound.assert_called_once()
        assert f.tasks.finish_bound.call_args.args[-1] == "failed"
        assert f.tasks.get_by_id(f.tasks.finish_bound.call_args.args[0]).status == "failed"


def test_same_persona_sessions_keep_distinct_task_leases_controls_and_hydration_bindings(app):
    from ananta_contracts.meet_avatar_video import REQUEST_SCHEMA

    with app.app_context():
        f = fixture()
        starts = [f.service.start(f.principal, "project", f.options | {"initial_persona": choice()}) for _ in range(2)]
        wires = [call.args[0] for call in f.worker.start_dialog.call_args_list]
        for key in ("task_id", "lease_id", "runtime_id", "session_id"):
            assert wires[0][key] != wires[1][key]
        assert wires[0]["initial_persona"] == wires[1]["initial_persona"]
        states = {
            wire["task_id"]: {
                "lease": {
                    "sessionId": "ms_" + str(index + 1) * 32,
                    "generation": 1,
                    "expiresAt": (f.f.now + 120) * 1000,
                },
                "peerId": "machine" + str(index),
                "roomId": wire["meeting"]["room_id"],
                "membershipEpoch": 3,
            }
            for index, wire in enumerate(wires)
        }
        f.service.meet.inspect.side_effect = lambda task, *_: deepcopy(states[task])
        requests = []
        for wire in wires:
            f.service.control(
                f.principal,
                "project",
                wire["task_id"],
                {
                    "expected_revision": 1,
                    "avatar": True,
                    "speech": False,
                    "chat": False,
                    "audio": False,
                    "screen": False,
                },
            )
            callback = {name: wire[name] for name in ("task_id", "lease_id", "runtime_id")}
            callback |= {"nonce": "a" * 32, "meet_session_id": states[wire["task_id"]]["lease"]["sessionId"]}
            projection = f.service.exchange(callback)["avatar"]
            assert projection["state"] == "ready"
            requests.append(
                {"schema": REQUEST_SCHEMA, "nonce": "a" * 32, "sent_at": f.f.now, "binding": projection["binding"]}
            )
        assert requests[0]["binding"]["selection_digest"] == requests[1]["binding"]["selection_digest"]
        assert requests[0]["binding"] != requests[1]["binding"]
        mixed = deepcopy(requests[0])
        mixed["binding"]["runtime_id"] = wires[1]["runtime_id"]
        with pytest.raises(ValueError):
            f.service.avatar_video(mixed)
        f.video.prepare.assert_not_called()
        f.service.inspect(f.principal, "project", starts[0]["task_id"], stop=True)
        with pytest.raises(ValueError):
            f.service.avatar_video(requests[0])
        remaining = f.service.inspect(f.principal, "project", starts[1]["task_id"])
        assert remaining["status"] == "in_progress" and remaining["controls"]["avatar"]["enabled"] is True
        clip = video_assignment()
        clip["reference"] = deepcopy(f.video.select.return_value[0])
        clip["repeat_mode"] = "hold_last"
        f.video.prepare.return_value = clip, deepcopy(PIN)
        assert f.service.avatar_video(requests[1])["video"]["reference"] == clip["reference"]
