"""Small runtime-only acoustic feature port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AcousticFeatureFrame:
    algorithm_version: str
    window_ms: int
    confidence: float
    pitch: tuple[float, ...]
    energy: tuple[float, ...]
    timing: tuple[float, ...]
    residual: tuple[float, ...] = ()

    def values(self) -> tuple[float, ...]:
        return (*self.pitch, *self.energy, *self.timing, *self.residual)


class AcousticFeatureExtractor(Protocol):
    def extract(
        self,
        *,
        pcm_s16le: bytes,
        sample_rate_hz: int,
        cancelled: callable | None = None,
    ) -> AcousticFeatureFrame: ...


__all__ = ["AcousticFeatureExtractor", "AcousticFeatureFrame"]
