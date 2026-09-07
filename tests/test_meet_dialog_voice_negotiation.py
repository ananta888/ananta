"""Actual Hub Task start/CAS/exchange; profile grants remain explicit test doubles."""

import time

import pytest

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_dialog import validate_assignment
from ananta_contracts.meet_speech import speech_profile
from tests.test_meet_dialog_avatar_negotiation import system as avatar_system
from tests.test_meet_dialog_transport import assignment
from tests.test_meet_dialog_voice_selection import PIN, voice_selection

pytestmark = pytest.mark.timeout(45)


def system():
    case = avatar_system()
    case.service.voices.profiles = case.profile_service
    case.service.voice_selections.profiles = case.profile_service
    case.service.voices.configured_profile = speech_profile(max_seconds=7)
    selected = voice_selection()
    case.profile_service.select.return_value = selected["reference"], selected["profile"]
    case.profile_service.prepare.return_value = (
        {"reference": selected["reference"], "speech_profile": speech_profile(max_seconds=7)},
        selected["profile"],
    )
    case.payload |= {"capabilities": ["speech.publish", "chat.read", "chat.send"], "chat_mode": "mention"}
    return case


@pytest.mark.parametrize("negotiated", [False, True])
def test_start_and_exchange_preserve_exact_legacy_wire_or_explicit_voice_protocol(app, negotiated):
    with app.app_context():
        case = system()
        started = case.service.start(
            case.principal, "project", case.payload | ({"voice_profiles": True} if negotiated else {})
        )
        wire = case.worker.start_dialog.call_args.args[0]
        assert validate_assignment(wire, case.f.now) == wire
        assert (wire.get("voice_profiles") is True) == negotiated
        assert "voice_selection" not in wire and "profile" not in wire
        status = case.service.inspect(case.principal, "project", started["task_id"])
        assert status["controls"]["speech"]["enabled"] is True
        assert ("voice_selection" in status) == negotiated
        case.meet.inspect.return_value = {"lease": {"expiresAt": (case.f.now + 120) * 1000}}
        callback = {key: wire[key] for key in ("task_id", "lease_id", "runtime_id")}
        callback |= {"nonce": "a" * 32, "meet_session_id": "ms_" + "a" * 32}
        original = case.service.exchange(callback)
        assert ("voice" in original) == negotiated
        if not negotiated:
            with pytest.raises(MeetError, match="not_negotiated"):
                case.service.select_voice(
                    case.principal, "project", started["task_id"], {"expected_revision": 1, "profile": PIN}
                )
            case.profile_service.select.assert_not_called()
            return
        assert original["voice"]["mode"] == "configured-piper-v1"
        assert original["voice"]["state"] == "ready"
        selected = case.service.select_voice(
            case.principal, "project", started["task_id"], {"expected_revision": 1, "profile": PIN}
        )
        assert selected["voice_selection"] == voice_selection()
        current = case.service.exchange(callback)
        assert current["voice"]["mode"] == "persona-voice-v1" and current["voice"]["speech_revision"] == 2
        assert current["controls"]["chat"] == original["controls"]["chat"]
        case.profile_service.prepare.side_effect = MeetError("synthetic-revocation", 403)
        blocked = case.service.exchange(callback)
        assert blocked["voice"]["state"] == "blocked" and blocked["voice"]["profile"] is None
        assert blocked["controls"] == current["controls"]
        assert case.tasks.get_by_id(started["task_id"]).status == "in_progress"


@pytest.mark.parametrize("flag", [False, 1, "true", None])
def test_malformed_voice_negotiation_cannot_start_or_validate_assignment(app, flag):
    with app.app_context():
        case = system()
        with pytest.raises(MeetError, match="voice_profiles_invalid"):
            case.service.start(case.principal, "project", case.payload | {"voice_profiles": flag})
        case.worker.start_dialog.assert_not_called()
        with pytest.raises(ValueError, match="voice_profiles_invalid"):
            validate_assignment(assignment() | {"voice_profiles": flag}, time.time())


@pytest.mark.parametrize("missing", ["profiles", "budget", "capability"])
def test_voice_negotiation_requires_capability_and_configured_profile_authority(app, missing):
    with app.app_context():
        case = system()
        if missing == "profiles":
            case.service.voices.profiles = None
        elif missing == "budget":
            case.service.voices.configured_profile = None
        else:
            case.payload["capabilities"].remove("speech.publish")
        with pytest.raises(MeetError, match="voice_profiles"):
            case.service.start(case.principal, "project", case.payload | {"voice_profiles": True})
        case.worker.start_dialog.assert_not_called()
