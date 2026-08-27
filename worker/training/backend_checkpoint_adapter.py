"""Exact compatibility gate for cross-attempt backend checkpoints."""

from __future__ import annotations

import secrets
from typing import Mapping

from worker.training.backends.base import TrainingBackendError

_FIELDS = (
    "backend",
    "backend_version",
    "base_model_sha256",
    "configuration_sha256",
    "dataset_sha256",
    "format",
)


def require_compatible_checkpoint(observed: Mapping[str, str], expected: Mapping[str, str]) -> None:
    if set(observed) != set(_FIELDS) or set(expected) != set(_FIELDS):
        raise TrainingBackendError("checkpoint_incompatible", "checkpoint binding is incomplete")
    for field in _FIELDS:
        left = observed.get(field)
        right = expected.get(field)
        if not isinstance(left, str) or not isinstance(right, str) or not secrets.compare_digest(left, right):
            raise TrainingBackendError("checkpoint_incompatible", f"checkpoint {field} binding differs")


__all__ = ["require_compatible_checkpoint"]
