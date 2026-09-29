"""Closed, separately bounded speech handoff; no live endpoint is enabled here."""

import base64
import copy
import io
import json
import wave

import pytest

from ananta_contracts.meet_dialog import MAX_DIALOG_BYTES, parse, request_signature, response_signature
from ananta_contracts.meet_speech import speech_profile
from ananta_contracts.meet_spoken_reply import (
    MAX_SPOKEN_BYTES,
    REQUEST_SCHEMA,
    RESPONSE_SCHEMA,
    decode_spoken_response,
    parse_spoken,
    spoken_request_signature,
    spoken_response_signature,
    validate_spoken_binding,
    validate_spoken_request,
)

NOW = 1788800000


def packet(samples=441):
    binding = {
        "tenant_id": "tenant",
        "project_id": "project",
        "task_id": "parent",
        "lease_id": "dispatch",
        "runtime_id": "runtime",
        "session_id": "session",
        "own_peer_id": "machine",
        "sender_peer_id": "human",
        "generation": 2,
        "membership_epoch": 3,
        "receive_revision": 4,
        "chat_revision": 1,
        "speech_revision": 1,
        "deadline_ms": (NOW + 60) * 1000,
        "meet_session_id": "ms_" + "a" * 32,
        "room_id": "room-" + "b" * 18,
    }
    request = {
        "schema": REQUEST_SCHEMA,
        "task_id": "parent",
        "lease_id": "dispatch",
        "runtime_id": "runtime",
        "nonce": "c" * 32,
        "sent_at": NOW,
        "meet_session_id": binding["meet_session_id"],
        "event": {
            "schema": "ananta.meet-chat-event.draft1",
            "session_id": "session",
            "generation": 2,
            "room_id": binding["room_id"],
            "membership_epoch": 3,
            "message_id": "message",
            "sender_peer_id": "human",
            "sender_kind": "human",
            "sent_at_ms": NOW * 1000,
            "text": "@ananta Hallo",
        },
    }
    wav = io.BytesIO()
    with wave.open(wav, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(22050)
        output.writeframes(b"\1\2" * samples)
    value = {
        "schema": RESPONSE_SCHEMA,
        "nonce": request["nonce"],
        "code": "generated",
        "reply": {
            "message_id": "message",
            "child_task_id": "child",
            "child_lease_id": "child-dispatch",
            "text": "Hallo",
            "binding": copy.deepcopy(binding),
            "audio": {"mime": "audio/wav", "base64": base64.b64encode(wav.getvalue()).decode()},
            "speech": {"profile": speech_profile(), "samples": samples},
            "duration_seconds": samples / 22050,
        },
    }
    return request, binding, value


def test_spoken_response_has_separate_size_and_signature_domains():
    request, binding, value = packet(882000)
    body = json.dumps(request).encode()
    raw = json.dumps(value).encode()
    assert MAX_DIALOG_BYTES < len(raw) < MAX_SPOKEN_BYTES
    assert validate_spoken_request(parse_spoken(body), NOW) == request
    reply = decode_spoken_response(parse_spoken(raw, response=True), request, binding, NOW * 1000)
    assert len(reply.pcm) == 1764000 and reply.text == "Hallo"
    assert "pcm" not in repr(reply) and "Hallo" not in repr(reply)
    with pytest.raises(ValueError):
        parse(raw)
    with pytest.raises(ValueError):
        parse_spoken(raw)
    key = b"synthetic-test-signing-key-32bytes"
    assert spoken_request_signature(key, body) != request_signature(key, body)
    signature = spoken_response_signature(key, body, raw)
    assert signature != response_signature(key, body, raw)
    assert signature != spoken_request_signature(key, raw)
    assert signature != spoken_response_signature(key, body + b" ", raw)
    assert signature != spoken_response_signature(key, body, raw + b" ")


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{" * 2000,
        b'{"a":1,"a":2}',
        b'{"x":{"a":1,"a":2}}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b"\xff",
        b" " * (MAX_SPOKEN_BYTES + 1),
    ],
)
def test_invalid_oversized_nonfinite_or_duplicate_json_never_decodes(raw):
    with pytest.raises(ValueError):
        parse_spoken(raw, response=True)


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "ananta.meet-dialog-callback.v1"},
        {"action": "chat"},
        {"nonce": "invalid"},
        {"sent_at": NOW - 11},
        {"sent_at": True},
        {"meet_session_id": "other"},
        {"extra": True},
        {"event": {}},
    ],
)
def test_request_keeps_existing_replay_age_identity_and_closed_field_bounds(change):
    request, _, _ = packet()
    with pytest.raises(ValueError):
        validate_spoken_request(request | change, NOW)


@pytest.mark.parametrize(
    "field",
    [
        "tenant_id",
        "project_id",
        "task_id",
        "lease_id",
        "runtime_id",
        "session_id",
        "own_peer_id",
        "sender_peer_id",
        "generation",
        "membership_epoch",
        "receive_revision",
        "chat_revision",
        "speech_revision",
        "deadline_ms",
        "meet_session_id",
        "room_id",
    ],
)
def test_every_binding_dimension_is_exact(field):
    request, binding, value = packet()
    old = value["reply"]["binding"][field]
    value["reply"]["binding"][field] = old + 1 if type(old) is int else old + "x"
    with pytest.raises(ValueError):
        decode_spoken_response(value, request, binding, NOW * 1000)


@pytest.mark.parametrize(
    "field", ["session_id", "sender_peer_id", "generation", "membership_epoch", "room_id", "message_id"]
)
def test_reply_is_bound_to_the_actual_requested_input(field):
    request, binding, value = packet()
    request["event"][field] = "foreign" if isinstance(request["event"][field], str) else 99
    with pytest.raises(ValueError):
        decode_spoken_response(value, request, binding, NOW * 1000)


@pytest.mark.parametrize("change", [{"nonce": "wrong"}, {"reply": None}, {"code": "rejected"}, {"extra": True}])
def test_generated_envelope_cannot_silently_drop_or_reinterpret_the_reply(change):
    request, binding, value = packet()
    with pytest.raises(ValueError):
        decode_spoken_response(value | change, request, binding, NOW * 1000)


@pytest.mark.parametrize(
    "change", [{"extra": True}, {"generation": True}, {"deadline_ms": 0}, {"speech_revision": 2**53}]
)
def test_unknown_or_ill_typed_binding_is_not_an_authority(change):
    _, binding, _ = packet()
    with pytest.raises(ValueError):
        validate_spoken_binding(binding | change)


@pytest.mark.parametrize(
    "change",
    [
        {"text": ""},
        {"text": "x" * 451},
        {"text": "\ud800"},
        {"text": "\0"},
        {"child_task_id": "bad/id"},
        {"child_lease_id": True},
        {"extra": True},
        {"duration_seconds": 2},
        {"audio": {"mime": "audio/mp3", "base64": ""}},
    ],
)
def test_text_child_task_and_media_fields_remain_closed(change):
    request, binding, value = packet()
    value["reply"].update(change)
    with pytest.raises(ValueError):
        decode_spoken_response(value, request, binding, NOW * 1000)


def test_expiry_profile_sample_claim_and_pcm_bytes_are_all_checked():
    request, binding, value = packet()
    with pytest.raises(ValueError):
        decode_spoken_response(value, request, binding, binding["deadline_ms"])
    for clock in (True, float("nan"), 0):
        with pytest.raises(ValueError):
            decode_spoken_response(value, request, binding, clock)
    for mutate in (
        lambda r: r["speech"].update(samples=442),
        lambda r: r["speech"]["profile"].update(voice_id="other"),
        lambda r: r["audio"].update(base64="AA=="),
        lambda r: r["audio"].update(base64="x" * 2666669),
    ):
        changed = copy.deepcopy(value)
        mutate(changed["reply"])
        with pytest.raises(ValueError):
            decode_spoken_response(changed, request, binding, NOW * 1000)


def test_bounded_no_reply_does_not_authorize_audio():
    request, binding, value = packet()
    assert (
        decode_spoken_response(value | {"code": "control_changed", "reply": None}, request, binding, NOW * 1000) is None
    )
