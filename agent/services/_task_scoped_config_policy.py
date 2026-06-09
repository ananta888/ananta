"""Bounded-value utilities for the task-scoped execution service (SPLIT-001b).

Extracted from ``agent.services.task_scoped_execution_service`` as the
first pure-utility tranche of the config_policy cluster. The functions
here own one concern: parsing + clamping numeric configuration values
to documented bounds, with a safe fallback if the input is unparseable.

Single Responsibility: bounded-value coercion. No policy reading, no
Flask app-context coupling, no cluster-specific business rules.

Backward compatibility: the parent module keeps the original static
methods (``_normalize_temperature``, ``_bounded_int``, ``_bounded_float``)
as thin delegating wrappers for 12 months (see SPLIT-001 in
``todos/todo.refactor-large-files-split.json``).
"""
from __future__ import annotations

from typing import Optional, Union


def normalize_temperature(value: Union[float, int, str, None]) -> Optional[float]:
    """Clamp a temperature value to the documented [0.0, 2.0] range.

    Returns ``None`` for unparseable input or explicit ``None``.
    """
    if value is None:
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    if normalized < 0.0:
        normalized = 0.0
    if normalized > 2.0:
        normalized = 2.0
    return normalized


def bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Parse ``value`` as int and clamp to ``[minimum, maximum]``.

    Falls back to ``default`` when the input is None or unparseable.
    """
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def bounded_float(
    value: object,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    """Parse ``value`` as float and clamp to ``[minimum, maximum]``.

    Falls back to ``default`` when the input is None or unparseable.
    """
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


# Public aliases that match the historical underscore-prefixed names.
_normalize_temperature = normalize_temperature
_bounded_int = bounded_int
_bounded_float = bounded_float

