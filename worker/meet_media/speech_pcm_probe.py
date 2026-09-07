"""Fixed synthetic phrase on actual CUDA; private test PCM, not release evidence."""

import base64
import json
import time

from worker.meet_media.audio_output import SAMPLE_RATE, speech_frames
from worker.meet_media.piper_speech import PiperSpeechSource


def run():
    started = time.monotonic()

    def current():
        if time.monotonic() - started >= 35:
            raise ValueError("test_speech_probe_expired")

    pcm = bytearray()
    frames = speech_frames("Hallo, Ananta spricht lokal.", PiperSpeechSource(), max_seconds=10, require_current=current)
    try:
        for frame in frames:
            if frame.start_sample != len(pcm) // 2:
                raise ValueError("test_speech_sample_clock_invalid")
            pcm.extend(frame.pcm_s16le)
        if not 441 < len(pcm) // 2 <= 10 * SAMPLE_RATE:
            raise ValueError("test_speech_pcm_invalid")
        return {
            "status": "passed",
            "classification": "synthetic_local_technical_observation",
            "engine": "piper-cuda",
            "sample_rate": SAMPLE_RATE,
            "samples": len(pcm) // 2,
            "pcm_base64": base64.b64encode(pcm).decode("ascii"),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "human_capture_used": False,
            "production_release_evidence": False,
        }
    finally:
        frames.close()
        pcm[:] = b"\0" * len(pcm)


if __name__ == "__main__":
    print(json.dumps(run()))
