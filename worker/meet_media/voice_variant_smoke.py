"""Real isolated GPU variant synthesis; no capture, meeting or release claim."""

import hashlib
import json
import time

import numpy as np

from ananta_contracts.meet_speech import speech_profile
from ananta_contracts.meet_voice_catalog import voice_preset
from worker.meet_media.audio_output import FRAME_SAMPLES, SAMPLE_RATE, speech_frames
from worker.meet_media.piper_speech import PiperSpeechSource


def variant(name, deadline):
    started = time.monotonic()
    revoked = False

    def current():
        if revoked or time.monotonic() >= deadline:
            raise PermissionError("synthetic_voice_variant_revoked")

    profile = speech_profile(voice_id="piper.de_DE.thorsten_emotional.medium." + name, max_seconds=10)
    source = PiperSpeechSource(profile=profile)
    count = samples = 0
    peak = 0.0
    digest = hashlib.sha256()
    first = None
    for frame in speech_frames("Hallo, diese Stimme spricht lokal.", source, max_seconds=10, require_current=current):
        if frame.start_sample != samples or not 0 < frame.samples <= FRAME_SAMPLES:
            raise ValueError("meet_voice_variant_clock_invalid")
        if first is None:
            first = round((time.monotonic() - started) * 1000)
        peak = max(peak, float(np.max(np.abs(np.frombuffer(frame.pcm_s16le, dtype="<i2").astype(np.int32)))) / 32768)
        digest.update(frame.pcm_s16le)
        samples += frame.samples
        count += 1
    if count < 2 or peak < 0.01:
        raise ValueError("meet_voice_variant_audio_missing")
    interrupted = speech_frames("Dieser Satz wird abgebrochen.", source, max_seconds=10, require_current=current)
    if next(interrupted).start_sample != 0:
        raise ValueError("meet_voice_variant_clock_not_reset")
    revoked = True
    cancelled_at = time.monotonic()
    try:
        next(interrupted)
    except PermissionError:
        pass
    else:
        raise ValueError("meet_voice_variant_cancel_failed")
    if next(interrupted, None) is not None:
        raise ValueError("meet_voice_variant_stale_pcm")
    return {
        "voice_id": profile["voice_id"],
        "speaker_id": voice_preset(profile["voice_id"]).speaker_id,
        "model_sha256": profile["model_sha256"],
        "config_sha256": profile["config_sha256"],
        "frames": count,
        "samples": samples,
        "peak": round(peak, 5),
        "pcm_sha256": digest.hexdigest(),
        "first_frame_ms_including_load": first,
        "local_checkpoint_cancel_ms": round((time.monotonic() - cancelled_at) * 1000),
    }


def run():
    started = time.monotonic()
    variants = [variant(name, started + 40) for name in ("neutral", "whisper")]
    return {
        "status": "passed",
        "classification": "synthetic_local_technical_observation",
        "engine": "piper-cuda",
        "sample_rate": SAMPLE_RATE,
        "variants": variants,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "human_capture_used": False,
        "meet_delivery_verified": False,
        "production_release_evidence": False,
    }


if __name__ == "__main__":
    print(json.dumps(run()))
