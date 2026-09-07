"""Real SQL chat admission with explicit voice-policy doubles; no real model or release claim."""

from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_voices import MeetDialogVoices
from ananta_contracts.meet_dialog_voice import content_digest, voice_binding_fields
from ananta_contracts.meet_speech import speech_profile
from ananta_contracts.meet_spoken_reply import decode_spoken_response
from tests.test_meet_dialog_spoken_reply import spoken as spoken
from tests.test_meet_dialog_voice_selection import voice_selection


def select_voice(case):
    fixture, service, _, _, _, _, _ = case
    selected = voice_selection()
    profile = speech_profile(voice_id="piper.de_DE.thorsten_emotional.medium.whisper")
    # Explicit adapter double: immutable binding correctness is tested with the
    # real voice catalog/Registry/profile services in test_meet_persona_voices.
    profiles = Mock(
        prepare=Mock(
            return_value=({"reference": selected["reference"], "speech_profile": profile}, selected["profile"])
        )
    )
    fixture.context["voice_selection"] = selected
    service.voices = MeetDialogVoices(profiles, speech_profile(), clock=lambda: fixture.now)
    return profiles, selected, profile


def test_selected_voice_goes_only_to_bound_generation_without_mutating_fixed_default(request):
    case = request.getfixturevalue("spoken")
    fixture, service, payload, _, worker, tasks, _ = case
    _, selected, profile = select_voice(case)
    response = service.execute(payload)
    reply = response["reply"]
    assert reply["speech"]["profile"] == profile
    assert worker.execute.call_args.args[0]["speech_profile"] == profile
    assert "hub_voice_selection" not in worker.execute.call_args.args[0]
    assert tasks.start.call_args.args[0]["hub_voice_selection"] == selected
    assert service.replies.speech_profile == speech_profile()
    assert reply["binding"]["voice_selection_digest"] == content_digest(selected)
    assert reply["binding"]["voice_profile_digest"] == content_digest(profile)
    assert decode_spoken_response(response, payload, reply["binding"], fixture.now * 1000).pcm


def test_negotiated_configured_mode_preserves_operator_profile_with_explicit_binding(request):
    case = request.getfixturevalue("spoken")
    fixture, service, payload, _, _, tasks, _ = case
    profiles, _, _ = select_voice(case)
    fixture.context["voice_selection"] = {"mode": "configured-piper-v1"}
    reply = service.execute(payload)["reply"]
    assert reply["speech"]["profile"] == speech_profile()
    assert tasks.start.call_args.args[0]["hub_voice_selection"] == {"mode": "configured-piper-v1"}
    profiles.prepare.assert_not_called()


def test_revoked_voice_blocks_generation_instead_of_falling_back_to_fixed_voice(request):
    case = request.getfixturevalue("spoken")
    _, service, payload, _, worker, _, _ = case
    profiles, _, _ = select_voice(case)
    profiles.prepare.side_effect = MeetError("synthetic_voice_revocation", 403)
    response = service.execute(payload)
    assert response["reply"] is None and response["code"] != "generated"
    worker.execute.assert_not_called()


@pytest.mark.parametrize("change", ["policy", "selection", "speech"])
def test_voice_change_during_inference_prevents_release_of_old_pcm(request, change):
    case = request.getfixturevalue("spoken")
    fixture, service, payload, _, worker, tasks, generate = case
    profiles, _, _ = select_voice(case)

    def changed(turn):
        value = generate(turn)
        if change == "policy":
            profiles.prepare.side_effect = MeetError("synthetic_voice_revocation", 403)
        elif change == "selection":
            fixture.context["voice_selection"] = {"mode": "configured-piper-v1"}
        else:
            fixture.context["controls"]["speech"]["enabled"] = False
        return value

    worker.execute.side_effect = changed
    with pytest.raises(MeetError, match="authority_changed"):
        service.execute(payload)
    assert tasks.finish.call_args.args[1] == "failed"


def test_speech_change_during_voice_policy_io_cannot_authorize_stale_reservation(request):
    case = request.getfixturevalue("spoken")
    fixture, service, payload, _, worker, _, _ = case
    profiles, _, _ = select_voice(case)
    result = profiles.prepare.return_value
    calls = 0

    def prepare(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            fixture.context["controls"]["speech"]["revision"] += 1
            fixture.context["controls"]["revision"] += 1
        return result

    profiles.prepare.side_effect = prepare
    assert service.execute(payload)["reply"] is None
    worker.execute.assert_not_called()


def test_voice_mode_requires_configured_service_and_never_silently_downgrades(request):
    case = request.getfixturevalue("spoken")
    fixture, service, payload, _, worker, _, _ = case
    fixture.context["voice_selection"] = {"mode": "configured-piper-v1"}
    with pytest.raises(MeetError, match="profiles_unavailable"):
        service.execute(payload)
    worker.execute.assert_not_called()


def test_voice_projection_is_independent_and_never_exposes_profile_when_paused(request):
    case = request.getfixturevalue("spoken")
    fixture, service, _, _, _, _, _ = case
    profiles, _, profile = select_voice(case)
    scope = fixture.authority.current("task", "dispatch", "runtime")
    projection = service.voices.projection(scope)
    assert projection["state"] == "ready" and voice_binding_fields(projection)[
        "voice_profile_digest"
    ] == content_digest(profile)
    fixture.context["controls"]["speech"]["enabled"] = False
    profiles.prepare.reset_mock()
    paused = service.voices.projection(fixture.authority.current("task", "dispatch", "runtime"))
    assert paused["state"] == "paused" and paused["profile"] is None
    profiles.prepare.assert_not_called()
