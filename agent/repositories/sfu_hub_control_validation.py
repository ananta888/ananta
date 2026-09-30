"""Shared validation primitives and error type for SFU Hub control repositories."""

from __future__ import annotations

import hashlib
import math
import re


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class SfuHubControlRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _plain_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_ref(value: object) -> bool:
    return isinstance(value, str) and bool(_SAFE_REF.fullmatch(value))


def _safe_reason(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and bool(re.fullmatch(r"[a-z0-9][a-z0-9_:-]*", value))
    )


def _finite_time(value: object, reason_code: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError(reason_code)
    return float(value)


def _positive_clock_ms(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise SfuHubControlRepositoryError(
            "sfu_reconciliation_clock_invalid"
        )
    return value


__all__ = [
    "SfuHubControlRepositoryError",
]
