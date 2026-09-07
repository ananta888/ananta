"""Immutable shipped voice definitions; availability and grants belong elsewhere."""

from dataclasses import dataclass

REVISION = "1162a9173d0ce503555aed757976b7a9912eae4c"
DEFAULT_VOICE_ID = "piper.de_DE.thorsten.medium"


@dataclass(frozen=True)
class VoiceModel:
    name: str
    repository_path: str
    revision: str
    model_sha256: str
    config_sha256: str
    model_max_bytes: int
    config_max_bytes: int
    speakers: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class VoicePreset:
    voice_id: str
    language: str
    model: VoiceModel
    speaker_id: int | None = None


DEFAULT_MODEL = VoiceModel(
    "de_DE-thorsten-medium.onnx",
    "de/de_DE/thorsten/medium",
    REVISION,
    "7e64762d8e5118bb578f2eea6207e1a35a8e0c30595010b666f983fc87bb7819",
    "974adee790533adb273a1ac88f49027d2a1b8f0f2cf4905954a4791e79264e85",
    70_000_000,
    65_536,
)
EMOTIONAL_MODEL = VoiceModel(
    "de_DE-thorsten_emotional-medium.onnx",
    "de/de_DE/thorsten_emotional/medium",
    REVISION,
    "c1764e652266cd6dcebf1b95c61973df5970a5f5272e94b655ff1ddf9a99d1ff",
    "92895b9e99f7cfc13f4a9879da615c3d6e0baa4d660e26d7b685abdd27a6d1d3",
    76_745_905,
    5031,
    (
        ("amused", 0),
        ("angry", 1),
        ("disgusted", 2),
        ("drunk", 3),
        ("neutral", 4),
        ("sleepy", 5),
        ("surprised", 6),
        ("whisper", 7),
    ),
)
PRESETS = (VoicePreset(DEFAULT_VOICE_ID, "de-DE", DEFAULT_MODEL),) + tuple(
    VoicePreset("piper.de_DE.thorsten_emotional.medium." + name, "de-DE", EMOTIONAL_MODEL, speaker)
    for name, speaker in EMOTIONAL_MODEL.speakers
)


def voice_preset(voice_id: str) -> VoicePreset:
    if type(voice_id) is str:
        for preset in PRESETS:
            if voice_id == preset.voice_id:
                return preset
    raise ValueError("meet_speech_profile_unsupported")
