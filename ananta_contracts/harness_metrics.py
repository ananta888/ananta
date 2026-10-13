"""Finite, immutable numeric observations for Harness ranking and accounting."""

import math
from collections.abc import Mapping
from types import MappingProxyType


def finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def freeze_metrics(values: Mapping) -> Mapping:
    if not isinstance(values, Mapping) or not values:
        raise ValueError("harness_metrics_invalid")
    if any(
        not isinstance(key, str) or not key or len(key) > 200 or not finite_number(value)
        for key, value in values.items()
    ):
        raise ValueError("harness_metrics_invalid")
    return MappingProxyType(dict(values))
