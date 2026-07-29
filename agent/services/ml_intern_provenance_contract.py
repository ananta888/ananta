"""Dependency-light canonical contract for provided ML-intern provenance IDs.

The module validates identifiers supplied by an authority. It deliberately
does not mint identifiers and does not decide whether a valid identifier is
trusted for a particular operation.
"""

from __future__ import annotations

import re
from typing import Any

_SOURCE_ID_RE = re.compile(r"^SRC_[A-Za-z0-9][A-Za-z0-9_.:-]{0,187}$")
_RUN_ID_RE = re.compile(r"^RUN_[A-Za-z0-9][A-Za-z0-9_.:-]{0,187}$")
_MAX_IDENTIFIERS = 128


class MlInternTrainingContractError(ValueError):
    """Backward-compatible public error for ML-intern contract violations."""

    def __init__(self, reason_code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code


def normalize_source_ids(value: Any) -> tuple[str, ...]:
    return _normalize_provenance_ids(
        value,
        field_name="source_ids",
        pattern=_SOURCE_ID_RE,
    )


def normalize_run_ids(value: Any) -> tuple[str, ...]:
    return _normalize_provenance_ids(
        value,
        field_name="run_ids",
        pattern=_RUN_ID_RE,
    )


def _normalize_provenance_ids(
    value: Any,
    *,
    field_name: str,
    pattern: re.Pattern[str],
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > _MAX_IDENTIFIERS:
        raise MlInternTrainingContractError(
            f"{field_name}_invalid",
            f"{field_name} must be a bounded array of provided identifiers",
        )
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise MlInternTrainingContractError(
                f"{field_name}_invalid",
                f"{field_name} contains a non-string identifier",
            )
        identifier = item.strip()
        if not pattern.fullmatch(identifier):
            raise MlInternTrainingContractError(
                f"{field_name}_invalid",
                f"{field_name} contains an invalid provided identifier",
            )
        if identifier in normalized:
            raise MlInternTrainingContractError(
                f"{field_name}_duplicate",
                f"{field_name} contains a duplicate identifier",
            )
        normalized.append(identifier)
    return tuple(sorted(normalized))


__all__ = [
    "MlInternTrainingContractError",
    "normalize_run_ids",
    "normalize_source_ids",
]
