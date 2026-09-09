"""Separate spoken-dialog envelope. Parsing never issues task/meeting authority."""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field

from ananta_contracts.meet_dialog import ID, MAX_DIALOG_BYTES, validate_callback
from ananta_contracts.meet_dialog_voice import VOICE_BINDING_FIELDS, content_digest, validate_voice_binding_fields
from ananta_contracts.meet_speaker_floor import validate_speaker_permit
from ananta_contracts.meet_speech_audio import decode_speech_wav

REQUEST_SCHEMA = "ananta.meet-dialog-speech-request.v1"
RESPONSE_SCHEMA = "ananta.meet-dialog-speech-response.v1"
MAX_SPOKEN_BYTES = 2_700_000
_BINDING_IDS = frozenset(
    {"tenant_id", "project_id", "task_id", "lease_id", "runtime_id", "session_id", "own_peer_id", "sender_peer_id"}
)
_BINDING_COUNTERS = frozenset(
    {"generation", "membership_epoch", "receive_revision", "chat_revision", "speech_revision", "deadline_ms"}
)


def parse_spoken(raw, *, response=False):
    maximum = MAX_SPOKEN_BYTES if response else MAX_DIALOG_BYTES
    if not isinstance(raw, bytes) or not 0 < len(raw) <= maximum:
        raise ValueError("meet_spoken_size_invalid")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("meet_spoken_duplicate_field")
            result[key] = value
        return result

    def number(_value):
        raise ValueError("meet_spoken_number_invalid")

    try:
        return json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=unique, parse_constant=number)
    except (UnicodeError, RecursionError):
        raise ValueError("meet_spoken_json_invalid") from None


def validate_spoken_request(value, now):
    fields = {"schema", "task_id", "lease_id", "runtime_id", "nonce", "sent_at", "meet_session_id", "event"}
    if not isinstance(value, dict) or set(value) != fields or value["schema"] != REQUEST_SCHEMA:
        raise ValueError("meet_spoken_request_invalid")
    # Reuse the small authenticated dialog request bounds; do not widen that API.
    validate_callback(value | {"schema": "ananta.meet-dialog-callback.v1", "action": "chat"}, now)
    event = value["event"]
    event_fields = {
        "schema",
        "session_id",
        "generation",
        "room_id",
        "membership_epoch",
        "message_id",
        "sender_peer_id",
        "sender_kind",
        "sent_at_ms",
        "text",
    }
    if set(event) != event_fields or event["schema"] != "ananta.meet-chat-event.draft1":
        raise ValueError("meet_spoken_event_invalid")
    # Semantic input validation/consent belongs to the existing Hub ChatEvent admission.
    return value


def validate_spoken_binding(binding):
    if (
        not isinstance(binding, dict)
        or set(binding) - VOICE_BINDING_FIELDS != _BINDING_IDS | _BINDING_COUNTERS | {"meet_session_id", "room_id"}
        or any(not isinstance(binding[k], str) or not ID.fullmatch(binding[k]) for k in _BINDING_IDS)
        or any(type(binding[k]) is not int or not 1 <= binding[k] < 2**53 for k in _BINDING_COUNTERS)
        or not isinstance(binding["meet_session_id"], str)
        or not re.fullmatch(r"ms_[A-Za-z0-9_-]{32}", binding["meet_session_id"])
        or not isinstance(binding["room_id"], str)
        or not re.fullmatch(r"room-[a-f0-9]{18}", binding["room_id"])
    ):
        raise ValueError("meet_spoken_binding_invalid")
    validate_voice_binding_fields(binding)
    return binding


@dataclass(frozen=True)
class SpokenReply:
    message_id: str
    child_task_id: str
    child_lease_id: str
    text: str = field(repr=False)
    pcm: bytes = field(repr=False)
    speaker_floor: dict | None = None


def decode_spoken_response(value, request, expected_binding, now_ms, *, floor_required=False):
    if type(floor_required) is not bool:
        raise ValueError("meet_speaker_negotiation_invalid")
    if type(now_ms) is not int or not 0 < now_ms < 2**53:
        raise ValueError("meet_spoken_clock_invalid")
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "nonce", "code", "reply"}
        or value["schema"] != RESPONSE_SCHEMA
        or value["nonce"] != request["nonce"]
        or not isinstance(value["code"], str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value["code"])
    ):
        raise ValueError("meet_spoken_response_invalid")
    if value["reply"] is None:
        if value["code"] == "generated":
            raise ValueError("meet_spoken_response_invalid")
        return None
    reply = value["reply"]
    fields = {"message_id", "child_task_id", "child_lease_id", "text", "binding", "audio", "speech", "duration_seconds"}
    if floor_required:
        fields |= {"speaker_floor"}
    if not isinstance(reply, dict) or set(reply) != fields or value["code"] != "generated":
        raise ValueError("meet_spoken_response_invalid")
    binding = validate_spoken_binding(reply["binding"])
    if (
        binding != validate_spoken_binding(expected_binding)
        or now_ms >= binding["deadline_ms"]
        or any(binding[k] != request[k] for k in ("task_id", "lease_id", "runtime_id", "meet_session_id"))
        or any(
            type(binding[k]) is not type(request["event"][k]) or binding[k] != request["event"][k]
            for k in ("session_id", "generation", "membership_epoch", "room_id", "sender_peer_id")
        )
        or reply["message_id"] != request["event"]["message_id"]
        or any(not isinstance(reply[k], str) or not ID.fullmatch(reply[k]) for k in ("child_task_id", "child_lease_id"))
    ):
        raise ValueError("meet_spoken_scope_mismatch")
    text = reply["text"]
    if (
        not isinstance(text, str)
        or not text.strip()
        or len(text) > 450
        or any(ord(c) < 32 and c not in "\n\t" for c in text)
    ):
        raise ValueError("meet_spoken_text_invalid")
    try:
        text.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("meet_spoken_text_invalid") from None
    floor = None
    if floor_required:
        floor = validate_speaker_permit(reply["speaker_floor"], deadline_ms=binding["deadline_ms"])
        if now_ms >= floor["expires_ms"]:
            raise ValueError("meet_speaker_permit_expired")
    pcm = decode_speech_wav(reply["audio"], reply["speech"], reply["duration_seconds"])
    if (
        "voice_profile_digest" in binding
        and content_digest(reply["speech"]["profile"]) != binding["voice_profile_digest"]
    ):
        raise ValueError("meet_spoken_voice_profile_mismatch")
    return SpokenReply(reply["message_id"], reply["child_task_id"], reply["child_lease_id"], text, pcm, floor)


def spoken_request_signature(key, body):
    return hmac.new(key, b"meet-dialog-speech-request-v1\0" + body, hashlib.sha256).hexdigest()


def spoken_response_signature(key, request_body, response_body):
    return hmac.new(
        key, b"meet-dialog-speech-response-v1\0" + hashlib.sha256(request_body).digest() + response_body, hashlib.sha256
    ).hexdigest()
