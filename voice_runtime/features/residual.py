"""Explicitly granted, lossy and bounded acoustic residual summaries."""

from __future__ import annotations

import array
import json
import math
from dataclasses import dataclass

MEASURED_PRIVACY_GATE_VERSION = "ananta.acoustic-residual-privacy.synthetic-attacks.v1"
MEASURED_PRIVACY_VERDICT = "no_go"


@dataclass(frozen=True, slots=True)
class ResidualPrivacyDecision:
    allowed: bool
    reason_code: str
    reconstructability_score: float
    speaker_linkability_score: float
    membership_inference_score: float


class AcousticResidualAdapter:
    algorithm_version = "ananta.acoustic-residual.block-energy.v1"
    max_values = 128
    max_serialized_bytes = 16 * 1024

    def __init__(self) -> None:
        self._buffer: tuple[float, ...] = ()

    def extract(self, *, grant_active: bool, pcm_s16le: bytes, sample_rate_hz: int) -> tuple[float, ...]:
        if not grant_active:
            self.purge()
            raise PermissionError("residual_feature_grant_required")
        if sample_rate_hz not in {8_000, 16_000, 24_000, 44_100, 48_000}:
            raise ValueError("residual_sample_rate_unsupported")
        if len(pcm_s16le) % 2 or len(pcm_s16le) > 320_000:
            raise ValueError("residual_pcm_budget_invalid")
        samples = array.array("h")
        samples.frombytes(pcm_s16le)
        if not samples:
            self._buffer = ()
            return ()
        block = max(1, math.ceil(len(samples) / self.max_values))
        values: list[float] = []
        for offset in range(0, len(samples), block):
            chunk = samples[offset : offset + block]
            rms = math.sqrt(sum((sample / 32768.0) ** 2 for sample in chunk) / len(chunk))
            # Six-bit effective precision intentionally removes phase and fine
            # spectral detail while retaining coarse correction cues.
            values.append(round(max(0.0, min(1.0, rms)) * 63) / 63)
        self._buffer = tuple(values[: self.max_values])
        encoded = json.dumps(self._buffer, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > self.max_serialized_bytes:
            self.purge()
            raise ValueError("residual_serialized_budget_exceeded")
        return self._buffer

    def assess_privacy(self, values: tuple[float, ...]) -> ResidualPrivacyDecision:
        if len(values) > self.max_values or any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
            return ResidualPrivacyDecision(False, "residual_invalid", 1.0, 1.0, 1.0)
        distinct = len(set(values)) / max(1, len(values))
        reconstructability = min(1.0, len(values) / self.max_values * distinct * 0.35)
        linkability = min(1.0, distinct * 0.25)
        membership = min(1.0, len(values) / self.max_values * 0.15)
        structurally_allowed = reconstructability <= 0.30 and linkability <= 0.25 and membership <= 0.15
        allowed = structurally_allowed and MEASURED_PRIVACY_VERDICT == "go"
        if allowed:
            reason_code = "residual_privacy_pass"
        elif structurally_allowed:
            reason_code = "residual_privacy_calibration_no_go"
        else:
            reason_code = "residual_privacy_no_go"
        return ResidualPrivacyDecision(
            allowed,
            reason_code,
            reconstructability,
            linkability,
            membership,
        )

    def purge(self) -> None:
        self._buffer = ()


__all__ = [
    "AcousticResidualAdapter",
    "MEASURED_PRIVACY_GATE_VERSION",
    "MEASURED_PRIVACY_VERDICT",
    "ResidualPrivacyDecision",
]
