"""Primitive validators for Kanban performance evidence."""

from __future__ import annotations

import math
from typing import Any


class SuiteValidationError(ValueError):
    pass


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SuiteValidationError(f"{label}_mapping_required")
    return value


def list_value(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise SuiteValidationError(f"{label}_list_required")
    return value


def text(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise SuiteValidationError(f"{label}_text_required")
    return result


def number(value: Any, label: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool):
        raise SuiteValidationError(f"{label}_number_required")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SuiteValidationError(f"{label}_number_required") from exc
    if not math.isfinite(result) or result < minimum:
        raise SuiteValidationError(f"{label}_number_invalid")
    return result


def integer(value: Any, label: str, *, minimum: int = 0) -> int:
    result = number(value, label, minimum=float(minimum))
    if not result.is_integer():
        raise SuiteValidationError(f"{label}_integer_required")
    return int(result)


def require_false(value: Any, label: str) -> None:
    if value is not False:
        raise SuiteValidationError(f"{label}_must_be_false")


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise SuiteValidationError("percentile_samples_required")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]
