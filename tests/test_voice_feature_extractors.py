from __future__ import annotations

import math
import struct

import pytest

from voice_runtime.features.prosody import BoundedProsodyExtractor


def _tone(samples: int = 1600) -> bytes:
    return b"".join(struct.pack("<h", round(math.sin(index / 9) * 8000)) for index in range(samples))


def test_prosody_is_finite_normalized_bounded_and_contains_no_pcm() -> None:
    frame = BoundedProsodyExtractor().extract(pcm_s16le=_tone(), sample_rate_hz=16_000)
    assert len(frame.values()) <= 32
    assert all(math.isfinite(value) and 0 <= value <= 1 for value in frame.values())
    assert not hasattr(frame, "pcm") and frame.algorithm_version.endswith("v1")


def test_silence_short_clipping_rate_nan_like_and_cancel_are_bounded() -> None:
    extractor = BoundedProsodyExtractor()
    assert extractor.extract(pcm_s16le=b"\0\0" * 10, sample_rate_hz=16_000).confidence == 0
    clipped = extractor.extract(pcm_s16le=struct.pack("<h", 32767) * 100, sample_rate_hz=48_000)
    assert clipped.energy[-1] == 1
    with pytest.raises(ValueError, match="sample_rate"):
        extractor.extract(pcm_s16le=b"\0\0", sample_rate_hz=12_345)
    with pytest.raises(RuntimeError, match="cancelled"):
        extractor.extract(pcm_s16le=b"\0\0", sample_rate_hz=16_000, cancelled=lambda: True)
