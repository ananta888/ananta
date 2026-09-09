"""Additive floor negotiation stays strict on both legacy and current paths."""

import copy

import pytest

from ananta_contracts.meet_dialog import validate_assignment, validate_callback
from ananta_contracts.meet_speaker_floor import validate_speaker_permit
from ananta_contracts.meet_spoken_reply import decode_spoken_response
from tests.test_meet_dialog_transport import assignment
from tests.test_meet_spoken_reply_contract import NOW, packet


def permit():
    return {"id": "a" * 64, "sequence": 1, "expires_ms": (NOW + 30) * 1000}


def test_negotiated_reply_requires_exact_floor_and_legacy_rejects_it():
    request, binding, value = packet()
    with pytest.raises(ValueError, match="response_invalid"):
        decode_spoken_response(value, request, binding, NOW * 1000, floor_required=True)
    value["reply"]["speaker_floor"] = permit()
    with pytest.raises(ValueError, match="response_invalid"):
        decode_spoken_response(value, request, binding, NOW * 1000)
    decoded = decode_spoken_response(value, request, binding, NOW * 1000, floor_required=True)
    assert decoded.speaker_floor == permit()
    value["reply"]["speaker_floor"]["sequence"] = 2
    assert decoded.speaker_floor["sequence"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"id": "SRC_invented"},
        {"id": "a" * 65},
        {"sequence": True},
        {"sequence": 0},
        {"sequence": 2**53},
        {"expires_ms": 1.0},
        {"expires_ms": False},
        {"priority": 2},
    ],
)
def test_invalid_permit_is_not_decoded(change):
    with pytest.raises(ValueError, match="permit_invalid"):
        validate_speaker_permit(permit() | change)


def test_expired_or_broadened_floor_cannot_release_pcm():
    request, binding, value = packet()
    value["reply"]["speaker_floor"] = permit()
    with pytest.raises(ValueError, match="permit_expired"):
        decode_spoken_response(value, request, binding, permit()["expires_ms"], floor_required=True)
    value["reply"]["speaker_floor"]["expires_ms"] = binding["deadline_ms"] + 1
    with pytest.raises(ValueError, match="permit_invalid"):
        decode_spoken_response(value, request, binding, NOW * 1000, floor_required=True)


def test_assignment_floor_is_explicit_true_and_requires_speech_capability():
    value = assignment()
    now = value["deadline"] - 120
    value["capabilities"] = sorted(set(value["capabilities"]) | {"speech.publish", "chat.read", "chat.send"})
    value["speaker_floor"] = True
    assert validate_assignment(value, now)["speaker_floor"] is True
    for flag in (False, 1, "true", None):
        with pytest.raises(ValueError, match="negotiation_invalid"):
            validate_assignment(value | {"speaker_floor": flag}, now)
    value["capabilities"].remove("speech.publish")
    with pytest.raises(ValueError, match="negotiation_invalid"):
        validate_assignment(value, now)


def test_exchange_completion_is_closed_and_cannot_be_used_on_other_actions():
    request, _binding, _response = packet()
    exchange = {k: v for k, v in request.items() if k != "event"}
    exchange.update(schema="ananta.meet-dialog-callback.v1", action="exchange", speech_finished=permit())
    assert validate_callback(exchange, NOW) == exchange
    for value in (None, True, {}, permit() | {"extra": True}):
        with pytest.raises(ValueError):
            validate_callback(exchange | {"speech_finished": value}, NOW)
    for action in ("audio", "chat", "finish"):
        with pytest.raises(ValueError):
            validate_callback(exchange | {"action": action}, NOW)
    legacy = copy.deepcopy(exchange)
    del legacy["speech_finished"]
    assert validate_callback(legacy, NOW) == legacy
