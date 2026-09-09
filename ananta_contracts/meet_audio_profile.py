"""Closed installed local ASR profile; neither media nor Workers select providers."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AudioReceiveProfile:
    language: str = "de"
    model: str = "whisper-small-pinned"
    vad: str = "local-vad-v1"
    segment_seconds: int = 10

    def __post_init__(self):
        if (
            not isinstance(self.language, str)
            or self.language not in ("de", "en")
            or self.model != "whisper-small-pinned"
            or not isinstance(self.vad, str)
            or self.vad not in ("local-vad-v1", "off")
            or type(self.segment_seconds) is not int
            or not 1 <= self.segment_seconds <= 10
        ):
            raise ValueError("meet_audio_profile_invalid")

    def projection(self):
        return {"schema": "ananta.meet-audio-profile.v1", **asdict(self)}

    @property
    def end_sample(self):
        return self.segment_seconds * 16000


def parse_audio_profile(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "language", "model", "vad", "segment_seconds"}
        or value["schema"] != "ananta.meet-audio-profile.v1"
    ):
        raise ValueError("meet_audio_profile_invalid")
    return AudioReceiveProfile(**{k: v for k, v in value.items() if k != "schema"})


def optional_audio_profile(container):
    return parse_audio_profile(container["audio_profile"]) if "audio_profile" in container else AudioReceiveProfile()
