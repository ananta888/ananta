"""Real private CUDA/Piper/NVENC encode and CPU decode, never live evidence."""

import json
import tempfile
import time
from pathlib import Path

from worker.meet_media.av_quality import verify_encoded_media
from worker.meet_media.avatar import avatar
from worker.meet_media.speech import speech


def run():
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="meet-av-quality-") as temporary:
        root = Path(temporary)
        audio = root / "speech.wav"
        samples, rate, duration = speech("Hallo, Ananta prüft Bild und Ton.", audio)
        video = avatar(audio, samples, rate, duration, root)
        report = verify_encoded_media(video.read_bytes(), expected_samples=len(samples), require_current=lambda: None)
    return report | {
        "status": "passed",
        "classification": "synthetic_local_technical_observation",
        "speech_samples": len(samples),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "human_capture_used": False,
        "production_release_evidence": False,
    }


if __name__ == "__main__":
    print(json.dumps(run()))
