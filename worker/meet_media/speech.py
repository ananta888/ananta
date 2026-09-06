"""German Piper CUDA adapter; refuses silent CPU fallback."""

import os
import wave

import numpy as np


def speech(text, target):
    import onnxruntime as ort
    from piper import PiperVoice

    ort.preload_dlls(directory="")
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        raise ValueError("meet_piper_cuda_unavailable")
    voice = PiperVoice.load(os.environ.get("MEET_PIPER_MODEL", "/models/de_DE-thorsten-medium.onnx"), use_cuda=True)
    if "CUDAExecutionProvider" not in voice.session.get_providers():
        raise ValueError("meet_piper_cuda_fallback_forbidden")
    with wave.open(str(target), "wb") as output:
        voice.synthesize_wav(text, output)
    with wave.open(str(target), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("meet_audio_format_invalid")
        duration = source.getnframes() / source.getframerate()
        if not 0 < duration <= 40:
            raise ValueError("meet_audio_duration_exceeded")
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        return samples, source.getframerate(), duration
