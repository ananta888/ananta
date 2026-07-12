"""Evaluation dataset and calibration-artifact validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

ALLOWED_SPLITS = frozenset({"ci", "hardware", "holdout"})
ALLOWED_DATA_CLASSES = frozenset({"synthetic", "public", "personal", "sensitive"})


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True)
class DatasetRecord:
    dataset_id: str
    source: str
    license: str
    sha256: str
    language: str
    data_class: str
    split: str
    consent_basis: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DatasetRecord":
        record = cls(**{field: str(raw.get(field) or "") for field in cls.__dataclass_fields__})
        record.validate()
        return record

    def validate(self) -> None:
        if not all((self.dataset_id, self.source, self.license, self.language, self.consent_basis)):
            raise ValueError("dataset provenance fields must not be empty")
        if self.split not in ALLOWED_SPLITS:
            raise ValueError("dataset split must be ci, hardware, or holdout")
        if self.data_class not in ALLOWED_DATA_CLASSES:
            raise ValueError("unknown dataset data class")
        if not self.sha256.startswith("sha256:") or len(self.sha256) != 71:
            raise ValueError("dataset sha256 must be a prefixed SHA-256 digest")


def load_dataset_manifest(path: str | Path) -> tuple[DatasetRecord, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "ananta-evaluation-dataset.v1":
        raise ValueError("unsupported dataset manifest schema")
    return tuple(DatasetRecord.from_mapping(item) for item in payload.get("datasets") or ())


@dataclass(frozen=True)
class CalibrationArtifact:
    backend: str
    model_revision: str
    dataset_version: str
    points: tuple[tuple[float, float], ...]
    digest: str

    @classmethod
    def build(
        cls, *, backend: str, model_revision: str, dataset_version: str, points: tuple[tuple[float, float], ...]
    ) -> "CalibrationArtifact":
        if not backend or not model_revision or not dataset_version:
            raise ValueError("calibration identity must be complete")
        normalized = tuple(sorted((float(raw), float(calibrated)) for raw, calibrated in points))
        if not normalized or any(not 0 <= value <= 1 for pair in normalized for value in pair):
            raise ValueError("calibration points must be within [0, 1]")
        canonical = json.dumps(
            {
                "backend": backend,
                "model_revision": model_revision,
                "dataset_version": dataset_version,
                "points": normalized,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return cls(backend, model_revision, dataset_version, normalized, _sha256(canonical))

    def calibrate(self, value: float, *, backend: str, model_revision: str, dataset_version: str) -> float:
        if (backend, model_revision, dataset_version) != (self.backend, self.model_revision, self.dataset_version):
            raise ValueError("calibration artifact identity mismatch")
        bounded = min(1.0, max(0.0, float(value)))
        lower = self.points[0]
        for upper in self.points[1:]:
            if bounded <= upper[0]:
                span = upper[0] - lower[0]
                if span <= 0:
                    return upper[1]
                ratio = (bounded - lower[0]) / span
                return lower[1] + ratio * (upper[1] - lower[1])
            lower = upper
        return lower[1]
