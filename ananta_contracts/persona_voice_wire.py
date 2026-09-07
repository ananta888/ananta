"""Voice-only dispatch/result wire; no model files, paths, speaker indices or grants."""

import base64

from ananta_contracts.persona_assignment import validate_persona_assignment
from ananta_contracts.persona_voice import (
    MAX_DESCRIPTOR_BYTES,
    InspectedVoiceDescriptor,
    inspect_voice_descriptor,
)


def validate_assignment(value, now):
    return validate_persona_assignment(value, now, schema="ananta.persona-voice-task.v1")


def encode_voice(value):
    if type(value) is not InspectedVoiceDescriptor or inspect_voice_descriptor(value.descriptor) != value:
        raise ValueError("persona_voice_inspection_invalid")
    return {
        "schema": "ananta.persona-voice-inspection.v1",
        "source_sha256": value.source_sha256,
        "descriptor": base64.b64encode(value.descriptor).decode("ascii"),
    }


def decode_voice(value, source_sha256):
    if (
        type(value) is not dict
        or set(value) != {"schema", "source_sha256", "descriptor"}
        or value["schema"] != "ananta.persona-voice-inspection.v1"
        or value["source_sha256"] != source_sha256
        or type(value["descriptor"]) is not str
        or len(value["descriptor"]) > 4 * ((MAX_DESCRIPTOR_BYTES + 2) // 3)
    ):
        raise ValueError("persona_voice_inspection_invalid")
    inspected = inspect_voice_descriptor(base64.b64decode(value["descriptor"], validate=True))
    if inspected.source_sha256 != source_sha256 or encode_voice(inspected) != value:
        raise ValueError("persona_voice_inspection_mismatch")
    return inspected
