"""Verify the pinned speech receipt against actual bounded WAV samples."""

import base64
import io
import math
import wave

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_speech import validate_speech_profile


def validate_speech_receipt(result):
    if "speech" not in result:
        return
    try:
        receipt = result["speech"]
        if not isinstance(receipt, dict) or set(receipt) != {"profile", "samples"}:
            raise ValueError()
        profile = validate_speech_profile(receipt["profile"])
        samples = receipt["samples"]
        if type(samples) is not int or not 0 < samples <= profile["max_seconds"] * profile["sample_rate"]:
            raise ValueError()
        encoded = result["audio"]["base64"]
        if not isinstance(encoded, str) or len(encoded) > 2_666_668:
            raise ValueError()
        pcm = base64.b64decode(encoded, validate=True)
        if len(pcm) < 44 or int.from_bytes(pcm[4:8], "little") + 8 != len(pcm):
            raise ValueError()
        with wave.open(io.BytesIO(pcm), "rb") as source:
            if (
                source.getnchannels() != 1
                or source.getsampwidth() != 2
                or source.getframerate() != profile["sample_rate"]
                or source.getnframes() != samples
                or len(source.readframes(samples + 1)) != samples * 2
            ):
                raise ValueError()
        duration = result["duration_seconds"]
        if (
            type(duration) not in (int, float)
            or not math.isfinite(duration)
            or abs(duration - samples / profile["sample_rate"]) > 0.00051
        ):
            raise ValueError()
    except (ValueError, TypeError, KeyError, wave.Error, EOFError):
        raise MeetError("meet_worker_speech_invalid", 502) from None


def validate_speech_binding(turn, result):
    if ("speech_profile" in turn) != ("speech" in result):
        raise MeetError("meet_worker_speech_mismatch", 502)
    if "speech_profile" in turn:
        validate_speech_receipt(result)
        if result["speech"]["profile"] != turn["speech_profile"]:
            raise MeetError("meet_worker_speech_mismatch", 502)
