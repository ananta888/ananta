"""Local Piper sentence adapter; fixed PCM format and no cloud/CPU fallback."""

import json

import numpy as np

from worker.meet_media.audio_output import SAMPLE_RATE
from worker.meet_media.piper_assets import load_pinned_assets


def load_cuda_voice(profile=None):
    import onnxruntime as ort
    from piper import PiperVoice
    from piper.config import PiperConfig

    model, config = load_pinned_assets(profile)
    ort.preload_dlls(directory="")
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        raise ValueError("meet_piper_cuda_unavailable")
    voice = PiperVoice(
        config=PiperConfig.from_dict(json.loads(config)),
        session=ort.InferenceSession(
            model,
            sess_options=ort.SessionOptions(),
            providers=[
                ("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"}),
            ],
        ),
        use_tashkeel=False,
    )
    if "CUDAExecutionProvider" not in voice.session.get_providers():
        raise ValueError("meet_piper_cuda_fallback_forbidden")
    voice.session.disable_fallback()
    if voice.config.sample_rate != SAMPLE_RATE:
        raise ValueError("meet_speech_sample_rate_unsupported")
    return voice


class PiperSpeechSource:
    def __init__(self, *, loader=None, profile=None):
        from ananta_contracts.meet_speech import speech_profile, validate_speech_profile

        self.profile = validate_speech_profile(profile if profile is not None else speech_profile())
        self.loader = loader if loader is not None else lambda: load_cuda_voice(self.profile)

    def synthesize(self, text, *, max_samples, require_current):
        require_current()
        voice = self.loader()
        require_current()
        produced = 0
        chunks = iter(voice.synthesize(text))
        try:
            while True:
                require_current()
                try:
                    chunk = next(chunks)
                except StopIteration:
                    return
                require_current()
                samples = chunk.audio_float_array
                if (
                    chunk.sample_rate != SAMPLE_RATE
                    or chunk.sample_width != 2
                    or chunk.sample_channels != 1
                    or not isinstance(samples, np.ndarray)
                    or samples.dtype != np.float32
                    or samples.ndim != 1
                    or not samples.size
                ):
                    raise ValueError("meet_audio_format_invalid")
                produced += samples.size
                if produced > min(max_samples, self.profile["max_seconds"] * SAMPLE_RATE):
                    raise ValueError("meet_audio_duration_exceeded")
                if not np.isfinite(samples).all() or np.any(np.abs(samples) > 1):
                    raise ValueError("meet_audio_samples_invalid")
                # Explicit little-endian output, independent of host byte order.
                yield (samples * 32767).astype("<i2").tobytes()
                del chunk, samples
        finally:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()
