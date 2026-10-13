"""Read admitted prediction bytes against a Hub-owned dataset projection.

Job/tenant/attempt authorization and immutable artifact admission belong to the
caller. This parser establishes byte and row bindings, never evidence identity
or production release eligibility. Worker scores are deliberately not consumed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, BinaryIO

MAX_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 512 * 1024
FIELDS = frozenset(
    {"id", "record_index", "base_output", "adapter_output", "expected_output", "base_score", "adapter_score", "winner"}
)


class SpecialistPredictionArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class SpecialistPredictionOutputs:
    base: tuple[str, ...]
    adapter: tuple[str, ...]


def _closed_json(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SpecialistPredictionArtifactError("specialist_prediction_duplicate_field")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise SpecialistPredictionArtifactError("specialist_prediction_nonfinite_json")


def read_specialist_predictions(
    stream: BinaryIO,
    *,
    admitted_sha256: str,
    admitted_size_bytes: int,
    expected_records: Sequence[Mapping[str, Any]],
) -> SpecialistPredictionOutputs:
    """Return raw outputs only after the entire artifact matches admitted bytes and rows."""
    if (
        not isinstance(admitted_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", admitted_sha256) is None
        or type(admitted_size_bytes) is not int
        or not 1 <= admitted_size_bytes <= MAX_BYTES
        or not expected_records
    ):
        raise SpecialistPredictionArtifactError("specialist_prediction_admission_invalid")
    digest = hashlib.sha256()
    total = count = 0
    seen: set[str] = set()
    base: list[str] = []
    adapter: list[str] = []
    while True:
        line = stream.readline(min(MAX_LINE_BYTES, admitted_size_bytes - total + 1) + 1)
        if not line:
            break
        total += len(line)
        if total > admitted_size_bytes or len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
            raise SpecialistPredictionArtifactError("specialist_prediction_bytes_invalid")
        digest.update(line)
        try:
            row = json.loads(line, object_pairs_hook=_closed_json, parse_constant=_reject_constant)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SpecialistPredictionArtifactError("specialist_prediction_json_invalid") from exc
        if (
            not isinstance(row, dict)
            or set(row) != FIELDS
            or count >= len(expected_records)
            or type(row["record_index"]) is not int
            or row["record_index"] != count
        ):
            raise SpecialistPredictionArtifactError("specialist_prediction_coverage_invalid")
        expected = expected_records[count]
        identity = hashlib.sha256(
            json.dumps(
                dict(expected), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode()
        ).hexdigest()
        reference = expected.get("output")
        if (
            identity in seen
            or row["id"] != identity
            or not isinstance(reference, str)
            or not reference
            or len(reference) > 16_000
            or row["expected_output"] != reference
        ):
            raise SpecialistPredictionArtifactError("specialist_prediction_dataset_mismatch")
        for field in ("base_output", "adapter_output"):
            if not isinstance(row[field], str) or len(row[field]) > 16_000:
                raise SpecialistPredictionArtifactError("specialist_prediction_output_invalid")
        seen.add(identity)
        base.append(row["base_output"])
        adapter.append(row["adapter_output"])
        count += 1
    if count != len(expected_records):
        raise SpecialistPredictionArtifactError("specialist_prediction_coverage_invalid")
    if total != admitted_size_bytes or digest.hexdigest() != admitted_sha256:
        raise SpecialistPredictionArtifactError("specialist_prediction_hash_mismatch")
    return SpecialistPredictionOutputs(tuple(base), tuple(adapter))
