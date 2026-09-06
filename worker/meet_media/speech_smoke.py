"""Synthetic private-container CUDA framing probe, not Meet publication evidence."""

import json
import time

from worker.meet_media.audio_output import FRAME_SAMPLES, SAMPLE_RATE, speech_frames
from worker.meet_media.piper_speech import PiperSpeechSource


def run():
    started = time.monotonic()
    deadline = started + 40
    revoked = False

    def checkpoint():
        if revoked or time.monotonic() >= deadline:
            raise PermissionError("synthetic_speech_probe_revoked")

    source = PiperSpeechSource()
    frames = speech_frames("Hallo, Ananta spricht lokal.", source, max_seconds=10, require_current=checkpoint)
    count = samples = 0
    first_ms = None
    for frame in frames:
        if frame.start_sample != samples or not 0 < frame.samples <= FRAME_SAMPLES:
            raise ValueError("speech_probe_clock_invalid")
        if first_ms is None:
            first_ms = round((time.monotonic() - started) * 1000)
        count += 1
        samples += frame.samples
    if count < 2:
        raise ValueError("speech_probe_audio_missing")
    # A new bounded execution starts its sample clock at zero and never reuses
    # the old generator or buffered output. No microphone or Meet sink exists.
    interrupted = speech_frames("Dieser Satz wird abgebrochen.", source, require_current=checkpoint)
    first = next(interrupted)
    if first.start_sample != 0:
        raise ValueError("speech_probe_clock_not_reset")
    revoked = True
    cancelled_at = time.monotonic()
    try:
        next(interrupted)
    except PermissionError:
        pass
    else:
        raise ValueError("speech_probe_cancel_failed")
    cancel_ms = round((time.monotonic() - cancelled_at) * 1000)
    if next(interrupted, None) is not None:
        raise ValueError("speech_probe_stale_audio")
    return {
        "status": "passed",
        "classification": "synthetic_local_technical_observation",
        "engine": "piper-cuda",
        "sample_rate": SAMPLE_RATE,
        "frames": count,
        "samples": samples,
        "audio_seconds": round(samples / SAMPLE_RATE, 3),
        "first_frame_ms_including_model_load": first_ms,
        "local_checkpoint_cancel_ms": cancel_ms,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "human_capture_used": False,
        "meet_delivery_verified": False,
        "production_release_evidence": False,
    }


if __name__ == "__main__":
    print(json.dumps(run()))
