"""Bounded, complete specialist prediction artifacts; no Hub orchestration."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from worker.training.backends.base import TrainingBackendError

MAX_PREDICTION_BYTES = 64 * 1024 * 1024
PREDICTION_ARTIFACT = "specialist_predictions.jsonl"


def persist_specialist_predictions(
    root: Path,
    samples: Iterable[Mapping[str, Any]],
    *,
    expected_records: int,
    check_cancelled: Callable[[], None],
) -> tuple[Path, list[dict[str, Any]], dict[str, int]]:
    """Persist every row, rejecting incomplete, duplicate or unordered coverage."""
    if type(expected_records) is not int or not 1 <= expected_records <= 10_000_000:
        raise TrainingBackendError("specialist_prediction_coverage_invalid", "expected record count is invalid")
    root.mkdir(parents=True, exist_ok=True)
    path = root / PREDICTION_ARTIFACT
    preview: list[dict[str, Any]] = []
    wins = {"base": 0, "adapter": 0, "tie": 0}
    identities: set[str] = set()
    total_bytes = count = 0
    try:
        handle = path.open("xb")
    except FileExistsError:
        raise TrainingBackendError(
            "specialist_prediction_artifact_exists", "prediction artifact already exists"
        ) from None
    try:
        with handle:
            for index, sample in enumerate(samples):
                check_cancelled()
                identity = sample.get("id")
                if (
                    sample.get("record_index") != index
                    or type(sample.get("record_index")) is not int
                    or not isinstance(identity, str)
                    or re.fullmatch(r"[0-9a-f]{64}", identity) is None
                    or identity in identities
                    or index >= expected_records
                ):
                    raise TrainingBackendError(
                        "specialist_prediction_coverage_invalid", "prediction coverage is invalid"
                    )
                winner = sample.get("winner")
                if winner not in wins:
                    raise TrainingBackendError("specialist_prediction_coverage_invalid", "prediction winner is invalid")
                row = dict(sample)
                encoded = (json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
                total_bytes += len(encoded)
                if total_bytes > MAX_PREDICTION_BYTES:
                    raise TrainingBackendError(
                        "specialist_prediction_quota_exceeded", "prediction artifact exceeds byte limit"
                    )
                handle.write(encoded)
                identities.add(identity)
                count += 1
                wins[winner] += 1
                if len(preview) < 20:
                    preview.append(row)
            if count != expected_records or count == 0:
                raise TrainingBackendError(
                    "specialist_prediction_coverage_invalid", "prediction coverage is incomplete"
                )
        return path, preview, wins
    except Exception:
        path.unlink(missing_ok=True)
        raise
