"""Bounded WAV/profile verification shared by Hub admission and Worker sinks."""

import base64
import io
import math
import wave

from ananta_contracts.meet_speech import validate_speech_profile

MAX_WAV_BYTES = 2_000_000
MAX_WAV_BASE64 = 2_666_668


def decode_speech_wav(audio, receipt, duration):
    try:
        if (
            not isinstance(audio, dict)
            or set(audio) != {"mime", "base64"}
            or audio["mime"] != "audio/wav"
            or not isinstance(receipt, dict)
            or set(receipt) != {"profile", "samples"}
        ):
            raise ValueError()
        profile = validate_speech_profile(receipt["profile"])
        samples = receipt["samples"]
        if type(samples) is not int or not 0 < samples <= profile["max_seconds"] * profile["sample_rate"]:
            raise ValueError()
        encoded = audio["base64"]
        if not isinstance(encoded, str) or not 0 < len(encoded) <= MAX_WAV_BASE64:
            raise ValueError()
        wav = base64.b64decode(encoded, validate=True)
        if not 44 <= len(wav) <= MAX_WAV_BYTES or int.from_bytes(wav[4:8], "little") + 8 != len(wav):
            raise ValueError()
        with wave.open(io.BytesIO(wav), "rb") as source:
            if (
                source.getnchannels() != 1
                or source.getsampwidth() != 2
                or source.getframerate() != profile["sample_rate"]
                or source.getnframes() != samples
            ):
                raise ValueError()
            pcm = source.readframes(samples + 1)
            if len(pcm) != samples * 2:
                raise ValueError()
        if (
            type(duration) not in (int, float)
            or not math.isfinite(duration)
            or abs(duration - samples / profile["sample_rate"]) > 0.00051
        ):
            raise ValueError()
        return pcm
    except (ValueError, TypeError, KeyError, wave.Error, EOFError):
        raise ValueError("meet_speech_audio_invalid") from None
