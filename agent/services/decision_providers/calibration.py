"""Validation-only temperature fitting. Calibration never authorizes promotion."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CalibrationSample:
    case_id: str
    split: str
    probabilities: tuple[float, ...]
    expected_index: int

    def __post_init__(self):
        if (
            self.split not in {"validation", "holdout", "train"}
            or len(self.probabilities) < 2
            or any(not math.isfinite(p) or not 0 <= p <= 1 for p in self.probabilities)
            or abs(sum(self.probabilities) - 1) > 0.01
            or type(self.expected_index) is not int
            or not 0 <= self.expected_index < len(self.probabilities)
        ):
            raise ValueError("decision_calibration_sample_invalid")


def scale_probabilities(probabilities: tuple[float, ...], temperature: float) -> tuple[float, ...]:
    if not math.isfinite(temperature) or not 0.1 <= temperature <= 100:
        raise ValueError("decision_calibration_temperature_invalid")
    values = [max(p, 1e-12) ** (1 / temperature) for p in probabilities]
    total = sum(values)
    return tuple(p / total for p in values)


def fit_temperature(samples: tuple[CalibrationSample, ...]) -> dict:
    if (
        not samples
        or any(s.split != "validation" for s in samples)
        or len({s.case_id for s in samples}) != len(samples)
    ):
        raise ValueError("decision_calibration_requires_unique_validation")
    grid = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0)
    losses = {}
    for temperature in grid:
        losses[temperature] = sum(
            -math.log(max(1e-12, scale_probabilities(s.probabilities, temperature)[s.expected_index])) for s in samples
        ) / len(samples)
    temperature = min(grid, key=lambda t: (losses[t], abs(t - 1)))
    # Tiny validation partitions cannot justify a fitted calibration profile.
    if len(samples) < 8:
        temperature = 1.0
    return {
        "schema": "ananta.decision-temperature-fit.v1",
        "samples": len(samples),
        "selection_split": "validation",
        "temperature": temperature,
        "status": "fitted_experimental" if len(samples) >= 8 else "insufficient_validation",
        "validation_nll": losses[temperature],
        "unscaled_validation_nll": losses[1.0],
        "validation_digest": hashlib.sha256(
            json.dumps([s.__dict__ for s in samples], sort_keys=True).encode()
        ).hexdigest(),
        "grants_permission": False,
        "production_release_evidence": False,
    }
