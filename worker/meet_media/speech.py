"""Compatibility WAV consumer for bounded, pull-based local speech output."""

import wave
from pathlib import Path

import numpy as np

from worker.meet_media.audio_output import SAMPLE_RATE, speech_frames
from worker.meet_media.piper_speech import PiperSpeechSource


def speech(text, target, *, source=None, max_seconds=40, require_current=lambda: None):
    target = Path(target)
    frames = speech_frames(
        text,
        source if source is not None else PiperSpeechSource(),
        max_seconds=max_seconds,
        require_current=require_current,
    )
    # Only a new task-local output is owned by this call. Never overwrite or
    # erase an existing file when generation fails.
    owned = False
    try:
        with target.open("xb") as raw:
            owned = True
            with wave.open(raw, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(SAMPLE_RATE)
                for frame in frames:
                    output.writeframesraw(frame.pcm_s16le)
        require_current()
        with wave.open(str(target), "rb") as wav:
            count = wav.getnframes()
            samples = np.frombuffer(wav.readframes(count), dtype="<i2").astype(np.float32) / 32768
        require_current()
        return samples, SAMPLE_RATE, count / SAMPLE_RATE
    except BaseException:
        if owned:
            target.unlink(missing_ok=True)
        raise
    finally:
        frames.close()
