"""Bounded local ASR child. No web server, Hub access, downloads or fallback."""

import base64
import json
import sys
from pathlib import Path

from voice_runtime.backends.faster_whisper import FasterWhisperBackend
from voice_runtime.preprocessing.audio_decode import AudioDecodeLimits, SafeAudioDecoder
from worker.meet_media.asr_model import REVISION, verify_model


def transcribe(payload):
    if not isinstance(payload, dict) or set(payload) != {"wav", "language"} or payload["language"] not in ("de", "en"):
        raise ValueError("meet_asr_input_invalid")
    wav = base64.b64decode(payload["wav"], validate=True)
    if not 44 < len(wav) <= 320_044:
        raise ValueError("meet_asr_input_invalid")
    model = Path("/models/faster-whisper-small")
    verify_model(model)

    def cuda_factory(*args, **kwargs):
        from faster_whisper import WhisperModel

        candidate = WhisperModel(*args, **kwargs)
        if candidate.model.device != "cuda" or candidate.model.compute_type != "float16":
            raise ValueError("meet_asr_cuda_required")
        return candidate

    backend = FasterWhisperBackend(
        model_path=str(model),
        model="whisper-small-pinned",
        device="cuda",
        compute_type="float16",
        beam_size=1,
        vad_filter=True,
        model_factory=cuda_factory,
        allow_download=False,
        decoder=SafeAudioDecoder(
            limits=AudioDecodeLimits(
                max_encoded_bytes=320_044,
                max_decoded_pcm_bytes=320_000,
                max_duration_ms=10_000,
                max_channels=1,
                max_sample_rate_hz=16_000,
                target_sample_rate_hz=16_000,
                ffmpeg_timeout_sec=3,
            )
        ),
    )
    result = backend.transcribe(filename="meet.wav", content=wav, language=payload["language"])
    if len(result.text) > 2000:
        raise ValueError("meet_asr_output_too_large")
    return {
        "schema": "ananta.meet-asr-result.v1",
        "text": result.text,
        "language": result.language,
        "duration_ms": result.duration_ms,
        "model_revision": REVISION,
        "device": "cuda",
    }


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(430_001)
        if len(raw) > 430_000:
            raise ValueError("meet_asr_input_too_large")
        print(json.dumps(transcribe(json.loads(raw))))
    except Exception:
        sys.stderr.write("meet_local_asr_failed\n")
        sys.exit(1)
