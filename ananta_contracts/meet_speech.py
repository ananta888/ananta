"""Closed local voice profile; immutable model pins are not evidence identities."""

from ananta_contracts.meet_voice_catalog import DEFAULT_MODEL, DEFAULT_VOICE_ID, voice_preset

REVISION = DEFAULT_MODEL.revision
MODEL_NAME = DEFAULT_MODEL.name
MODEL_SHA256 = DEFAULT_MODEL.model_sha256
CONFIG_SHA256 = DEFAULT_MODEL.config_sha256


def speech_profile(*, max_seconds=40, voice_id=DEFAULT_VOICE_ID):
    if type(max_seconds) is not int or not 1 <= max_seconds <= 40:
        raise ValueError("meet_speech_budget_invalid")
    preset = voice_preset(voice_id)
    return {
        "schema": "ananta.meet-speech-profile.v1",
        "voice_id": preset.voice_id,
        "language": preset.language,
        "model_revision": preset.model.revision,
        "model_sha256": preset.model.model_sha256,
        "config_sha256": preset.model.config_sha256,
        "sample_rate": 22050,
        "channels": 1,
        "sample_format": "pcm_s16le",
        "max_seconds": max_seconds,
    }


def validate_speech_profile(value):
    if not isinstance(value, dict) or set(value) != set(speech_profile()):
        raise ValueError("meet_speech_profile_invalid")
    expected = speech_profile(max_seconds=value["max_seconds"], voice_id=value["voice_id"])
    if any(type(value[name]) is not type(item) or value[name] != item for name, item in expected.items()):
        raise ValueError("meet_speech_profile_unsupported")
    return dict(value)
