"""Closed canonical preset descriptors; metadata never proves availability or consent."""

import hashlib
import json
from dataclasses import dataclass

from ananta_contracts.meet_speech import speech_profile, validate_speech_profile

MAX_DESCRIPTOR_BYTES = 2048
MEDIA_TYPE = "application/vnd.ananta.persona-voice+json"
SCHEMA = "ananta.persona-voice-preset.v1"


def voice_descriptor(voice_id):
    value = {"schema": SCHEMA, "speech": speech_profile(voice_id=voice_id)}
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class InspectedVoiceDescriptor:
    descriptor: bytes
    voice_id: str
    source_sha256: str


def inspect_voice_descriptor(content, media_type=MEDIA_TYPE):
    if type(content) is not bytes or not 0 < len(content) <= MAX_DESCRIPTOR_BYTES or media_type != MEDIA_TYPE:
        raise ValueError("persona_voice_input_invalid")
    try:
        value = json.loads(content)
        if type(value) is not dict or set(value) != {"schema", "speech"} or value["schema"] != SCHEMA:
            raise ValueError
        profile = validate_speech_profile(value["speech"])
        # Exact bytes reject duplicate keys, alternative encodings, hidden fields
        # and caller-selected budgets. Runtime budgets belong to the Hub task.
        if content != voice_descriptor(profile["voice_id"]):
            raise ValueError
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ValueError("persona_voice_descriptor_invalid") from None
    return InspectedVoiceDescriptor(content, profile["voice_id"], hashlib.sha256(content).hexdigest())
