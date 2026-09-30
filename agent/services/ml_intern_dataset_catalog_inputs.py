"""Normalization of caller-supplied dataset catalog request fields.

Each function either returns the canonical value or raises
:class:`DatasetCatalogError` with a stable reason code (SRP: request
validation is kept apart from catalog persistence)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent.services.ml_intern_dataset_catalog_errors import DatasetCatalogError


def dataset_extension(filename: str) -> str:
    clean = Path(str(filename or "")).name
    if clean != str(filename) or not clean:
        raise DatasetCatalogError("invalid_filename", "dataset filename is invalid")
    return Path(clean).suffix.lower()


def dataset_format(value: str) -> str:
    normalized = str(value or "instruction").strip().lower()
    if normalized not in {"instruction", "chat"}:
        raise DatasetCatalogError("invalid_dataset_format", "dataset format must be instruction or chat")
    return normalized


def partition_name(value: str) -> str:
    normalized = str(value or "train").strip().lower()
    if normalized not in {"train", "validation"}:
        raise DatasetCatalogError("invalid_partition", "partition must be train or validation")
    return normalized


def bounded_name(value: str) -> str:
    normalized = " ".join(str(value or "").split()).strip()
    if not normalized:
        raise DatasetCatalogError("dataset_name_required", "dataset name is required")
    return normalized[:160]


def idempotency_key(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 200 or any(ord(char) < 32 for char in normalized):
        raise DatasetCatalogError("invalid_idempotency_key", "idempotency key is invalid")
    return normalized


def request_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


__all__ = [
    "bounded_name",
    "dataset_extension",
    "dataset_format",
    "idempotency_key",
    "partition_name",
    "request_digest",
]
