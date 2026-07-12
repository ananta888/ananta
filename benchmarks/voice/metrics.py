"""Dependency-free voice quality and calibration metrics.

The functions are intentionally pure so the same implementation is used by CI,
hardware runs and offline release-gate verification.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

_WORD_RE = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)
_NUMBER_RE = re.compile(r"(?<!\w)[+-]?(?:\d+(?:[.,]\d+)?)(?!\w)")


def _tokens(text: str) -> list[str]:
    return [item.casefold() for item in _WORD_RE.findall(text)]


def edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    """Return Levenshtein distance using bounded O(min(n,m)) memory."""

    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for row, ref_item in enumerate(reference, start=1):
        current = [row]
        for column, hyp_item in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (ref_item != hyp_item),
                )
            )
        previous = current
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    reference_tokens = _tokens(reference)
    return edit_distance(reference_tokens, _tokens(hypothesis)) / max(1, len(reference_tokens))


def character_error_rate(reference: str, hypothesis: str) -> float:
    normalized_reference = "".join(reference.casefold().split())
    normalized_hypothesis = "".join(hypothesis.casefold().split())
    return edit_distance(list(normalized_reference), list(normalized_hypothesis)) / max(1, len(normalized_reference))


def multiset_recall(reference_items: Iterable[str], hypothesis_items: Iterable[str]) -> float:
    reference = Counter(item.casefold() for item in reference_items)
    hypothesis = Counter(item.casefold() for item in hypothesis_items)
    correct = sum(min(count, hypothesis[item]) for item, count in reference.items())
    return correct / max(1, sum(reference.values()))


def number_accuracy(reference: str, hypothesis: str) -> float:
    return multiset_recall(_NUMBER_RE.findall(reference), _NUMBER_RE.findall(hypothesis))


def timestamp_mean_absolute_error(
    reference: Sequence[tuple[int, int]],
    hypothesis: Sequence[tuple[int, int]],
    *,
    missing_penalty_ms: float,
) -> float:
    """Return endpoint MAE with an explicit penalty for missing word times."""

    penalty = float(missing_penalty_ms)
    if penalty < 0 or not math.isfinite(penalty):
        raise ValueError("missing timestamp penalty must be finite and non-negative")
    for collection in (reference, hypothesis):
        if any(start < 0 or end < start for start, end in collection):
            raise ValueError("timestamp ranges must be ordered and non-negative")
    count = max(len(reference), len(hypothesis))
    if count == 0:
        return 0.0
    errors: list[float] = []
    for index in range(count):
        if index >= len(reference) or index >= len(hypothesis):
            errors.append(penalty)
            continue
        reference_start, reference_end = reference[index]
        hypothesis_start, hypothesis_end = hypothesis[index]
        errors.append((abs(reference_start - hypothesis_start) + abs(reference_end - hypothesis_end)) / 2)
    return sum(errors) / count


def provenance_coverage(*, total_tokens: int, provenanced_tokens: int) -> float:
    if total_tokens < 0 or provenanced_tokens < 0 or provenanced_tokens > total_tokens:
        raise ValueError("provenance token counts are invalid")
    return 1.0 if total_tokens == 0 else provenanced_tokens / total_tokens


def expected_calibration_error(
    confidences: Sequence[float], outcomes: Sequence[bool], *, bins: int = 10
) -> float:
    if len(confidences) != len(outcomes):
        raise ValueError("confidences and outcomes must have equal length")
    if bins < 1:
        raise ValueError("bins must be positive")
    if not confidences:
        return 0.0
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for confidence, outcome in zip(confidences, outcomes, strict=True):
        bounded = min(1.0, max(0.0, float(confidence)))
        buckets[min(bins - 1, int(bounded * bins))].append((bounded, outcome))
    total = len(confidences)
    return sum(
        (len(bucket) / total)
        * abs(
            sum(confidence for confidence, _ in bucket) / len(bucket)
            - sum(outcome for _, outcome in bucket) / len(bucket)
        )
        for bucket in buckets
        if bucket
    )


def brier_score(confidences: Sequence[float], outcomes: Sequence[bool]) -> float:
    if len(confidences) != len(outcomes):
        raise ValueError("confidences and outcomes must have equal length")
    if not confidences:
        return 0.0
    squared_errors = (
        (min(1.0, max(0.0, float(value))) - float(outcome)) ** 2
        for value, outcome in zip(confidences, outcomes, strict=True)
    )
    return sum(squared_errors) / len(confidences)


@dataclass(frozen=True)
class VoiceEvaluation:
    schema_version: str
    wer: float
    cer: float
    named_entity_accuracy: float
    number_accuracy: float
    latency_ms: float
    real_time_factor: float
    timestamp_mae_ms: float | None = None
    provenance_coverage: float | None = None
    peak_ram_mb: float | None = None
    peak_vram_mb: float | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_voice_sample(
    *,
    reference: str,
    hypothesis: str,
    reference_entities: Sequence[str] = (),
    hypothesis_entities: Sequence[str] = (),
    latency_ms: float,
    audio_duration_ms: float,
    reference_timestamps: Sequence[tuple[int, int]] = (),
    hypothesis_timestamps: Sequence[tuple[int, int]] = (),
    missing_timestamp_penalty_ms: float = 1_000.0,
    total_tokens: int | None = None,
    provenanced_tokens: int | None = None,
    peak_ram_mb: float | None = None,
    peak_vram_mb: float | None = None,
) -> VoiceEvaluation:
    if latency_ms < 0 or audio_duration_ms <= 0 or not math.isfinite(latency_ms + audio_duration_ms):
        raise ValueError("latency and audio duration must be finite and non-negative")
    if (total_tokens is None) != (provenanced_tokens is None):
        raise ValueError("both provenance token counts must be supplied together")
    return VoiceEvaluation(
        schema_version="ananta.voice-evaluation.v1",
        wer=word_error_rate(reference, hypothesis),
        cer=character_error_rate(reference, hypothesis),
        named_entity_accuracy=multiset_recall(reference_entities, hypothesis_entities),
        number_accuracy=number_accuracy(reference, hypothesis),
        latency_ms=float(latency_ms),
        real_time_factor=float(latency_ms) / float(audio_duration_ms),
        timestamp_mae_ms=timestamp_mean_absolute_error(
            reference_timestamps,
            hypothesis_timestamps,
            missing_penalty_ms=missing_timestamp_penalty_ms,
        )
        if reference_timestamps or hypothesis_timestamps
        else None,
        provenance_coverage=provenance_coverage(
            total_tokens=total_tokens,
            provenanced_tokens=provenanced_tokens,
        )
        if total_tokens is not None and provenanced_tokens is not None
        else None,
        peak_ram_mb=peak_ram_mb,
        peak_vram_mb=peak_vram_mb,
    )
