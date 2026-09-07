"""Independent voice projection/PCM binding fences; deterministic, fully headless."""

import copy

import pytest

from ananta_contracts.meet_dialog_voice import content_digest, validate_voice_projection, voice_binding_fields
from ananta_contracts.meet_speech import speech_profile
from ananta_contracts.meet_spoken_reply import decode_spoken_response, validate_spoken_binding
from tests.test_meet_dialog_speech_output import fixture
from tests.test_meet_spoken_reply_contract import NOW


def ready():
    return {
        "mode": "persona-voice-v1",
        "state": "ready",
        "speech_revision": 1,
        "selection_digest": "a" * 64,
        "profile": speech_profile(),
    }


def negotiated():
    case = fixture()
    case.assignment["voice_profiles"] = True
    case.voice = ready()
    case.output.update(case.receipt, case.controls, case.voice)
    return case


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "cloned"},
        {"state": "warming"},
        {"speech_revision": True},
        {"speech_revision": 1024},
        {"selection_digest": "unbound"},
        {"state": "blocked"},
        {"profile": None},
        {"model_url": "https://caller"},
    ],
)
def test_projection_is_closed_and_nonready_never_carries_a_profile(change):
    with pytest.raises(ValueError):
        validate_voice_projection(ready() | change)


def test_ready_projection_has_exact_paired_content_pins_and_defensive_profile_copy():
    value = ready()
    copied = validate_voice_projection(value)
    value["profile"]["max_seconds"] = 1
    assert copied["profile"]["max_seconds"] == 40
    assert voice_binding_fields(copied) == {
        "voice_selection_digest": "a" * 64,
        "voice_profile_digest": content_digest(speech_profile()),
    }
    for state in ("paused", "blocked"):
        with pytest.raises(ValueError, match="unavailable"):
            voice_binding_fields(copied | {"state": state, "profile": None})


def test_spoken_response_must_match_negotiated_profile_not_just_sample_format():
    case = negotiated()
    binding = case.output.prepare(case.request["event"])
    assert set(binding) - set(case.expected) == {"voice_selection_digest", "voice_profile_digest"}
    packet = copy.deepcopy(case.value)
    packet["reply"]["binding"] = binding
    assert decode_spoken_response(packet, case.request, binding, NOW * 1000).pcm == case.result.pcm
    packet["reply"]["speech"]["profile"] = speech_profile(voice_id="piper.de_DE.thorsten_emotional.medium.whisper")
    with pytest.raises(ValueError, match="voice_profile_mismatch"):
        decode_spoken_response(packet, case.request, binding, NOW * 1000)


def test_partial_or_invalid_voice_binding_is_not_a_legacy_binding():
    case = fixture()
    for change in (
        {"voice_selection_digest": "a" * 64},
        {"voice_profile_digest": "b" * 64},
        {"voice_selection_digest": True, "voice_profile_digest": "b" * 64},
    ):
        with pytest.raises(ValueError):
            validate_spoken_binding(case.expected | change)
    assert validate_spoken_binding(case.expected) == case.expected


@pytest.mark.parametrize("playing", [False, True])
def test_observed_voice_revocation_cannot_revive_old_reply_after_ready_returns(playing):
    case = negotiated()
    binding = case.output.prepare(case.request["event"])
    if playing:
        assert case.output.accept(case.result, binding)
        case.output.tick()
    case.output.update(case.receipt, case.controls, case.voice | {"state": "blocked", "profile": None})
    assert not case.output.busy and case.output.pcm == b""
    case.output.update(case.receipt, case.controls, case.voice)
    assert not case.output.accept(case.result, binding)
    assert not case.output.busy
    fresh = case.output.prepare(case.request["event"])
    assert case.output.accept(case.result, fresh)


def test_unchanged_fresh_voice_projection_keeps_pending_reply_valid():
    case = negotiated()
    binding = case.output.prepare(case.request["event"])
    case.output.update(case.receipt, case.controls, copy.deepcopy(case.voice))
    assert case.output.accept(case.result, binding)


def test_changed_voice_selection_or_budget_invalidates_old_pcm():
    for change in ({"selection_digest": "b" * 64}, {"profile": speech_profile(max_seconds=10)}):
        case = negotiated()
        binding = case.output.prepare(case.request["event"])
        assert case.output.accept(case.result, binding)
        case.output.update(case.receipt, case.controls, case.voice | change)
        assert not case.output.busy and not case.output.accept(case.result, binding)


def test_invalid_or_unnegotiated_projection_closes_source():
    for negotiated_flag in (True, False):
        case = negotiated() if negotiated_flag else fixture()
        binding = case.output.prepare(case.request["event"])
        assert case.output.accept(case.result, binding)
        with pytest.raises(ValueError):
            case.output.update(case.receipt, case.controls, ready() | {"speech_revision": 2})
        assert not case.output.busy and case.output.fresh_until == 0
