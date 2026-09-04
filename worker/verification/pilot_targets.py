"""Small deterministic targets used by the bounded CrossHair pilot."""

from __future__ import annotations


def clamp(value: int, lower: int, upper: int) -> int:
    """Clamp a value.

    pre: lower <= upper
    post: lower <= __return__ <= upper
    """

    return min(upper, max(lower, value))


def normalize_identifier(value: str) -> str:
    """Normalize an identifier.

    post: __return__ == __return__.strip().lower()
    post: normalize_identifier(__return__) == __return__
    """

    return "-".join(value.strip().lower().split())


def unique_in_order(values: list[int]) -> list[int]:
    """Return first occurrences.

    post: len(__return__) <= len(values)
    post: all(item in values for item in __return__)
    """

    return list(dict.fromkeys(values))


def permission_subset_is_monotone(required: set[str], granted: set[str]) -> bool:
    """Return whether every required capability is granted.

    post: __return__ == required.issubset(granted)
    """

    return required <= granted


def normalize_dependencies(values: list[str], task_id: str) -> list[str]:
    """Normalize dependency identifiers without self-dependencies.

    post: task_id not in __return__
    post: len(__return__) == len(set(__return__))
    """

    return sorted({value.strip() for value in values if value.strip() and value.strip() != task_id})


def intentionally_wrong_abs(value: int) -> int:
    """Seeded defect proving concrete counterexample handling.

    post: __return__ >= 0
    """

    return value


def equivalent_clamp(value: int, lower: int, upper: int) -> int:
    return min(upper, max(lower, value))


def changed_clamp(value: int, lower: int, upper: int) -> int:
    return min(upper, max(lower, value)) + 1


__all__ = [
    "changed_clamp",
    "clamp",
    "equivalent_clamp",
    "intentionally_wrong_abs",
    "normalize_dependencies",
    "normalize_identifier",
    "permission_subset_is_monotone",
    "unique_in_order",
]
