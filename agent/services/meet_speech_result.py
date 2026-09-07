"""Verify the pinned speech receipt against actual bounded WAV samples."""

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_speech_audio import decode_speech_wav


def validate_speech_receipt(result):
    if "speech" not in result:
        return
    try:
        decode_speech_wav(result["audio"], result["speech"], result["duration_seconds"])
    except (ValueError, TypeError, KeyError):
        raise MeetError("meet_worker_speech_invalid", 502) from None


def validate_speech_binding(turn, result):
    if ("speech_profile" in turn) != ("speech" in result):
        raise MeetError("meet_worker_speech_mismatch", 502)
    if "speech_profile" in turn:
        validate_speech_receipt(result)
        if result["speech"]["profile"] != turn["speech_profile"]:
            raise MeetError("meet_worker_speech_mismatch", 502)
