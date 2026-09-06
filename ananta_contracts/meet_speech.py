"""Closed local voice profile; immutable model pins are not evidence identities."""

REVISION = "1162a9173d0ce503555aed757976b7a9912eae4c"
MODEL_NAME = "de_DE-thorsten-medium.onnx"
MODEL_SHA256 = "7e64762d8e5118bb578f2eea6207e1a35a8e0c30595010b666f983fc87bb7819"
CONFIG_SHA256 = "974adee790533adb273a1ac88f49027d2a1b8f0f2cf4905954a4791e79264e85"


def speech_profile(*, max_seconds=40):
    if type(max_seconds) is not int or not 1 <= max_seconds <= 40:
        raise ValueError("meet_speech_budget_invalid")
    return {
        "schema": "ananta.meet-speech-profile.v1",
        "voice_id": "piper.de_DE.thorsten.medium",
        "language": "de-DE",
        "model_revision": REVISION,
        "model_sha256": MODEL_SHA256,
        "config_sha256": CONFIG_SHA256,
        "sample_rate": 22050,
        "channels": 1,
        "sample_format": "pcm_s16le",
        "max_seconds": max_seconds,
    }


def validate_speech_profile(value):
    if not isinstance(value, dict) or set(value) != set(speech_profile()):
        raise ValueError("meet_speech_profile_invalid")
    expected = speech_profile(max_seconds=value["max_seconds"])
    if any(type(value[name]) is not type(item) or value[name] != item for name, item in expected.items()):
        raise ValueError("meet_speech_profile_unsupported")
    return dict(value)
