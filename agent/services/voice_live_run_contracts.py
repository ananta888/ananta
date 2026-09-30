"""Bounds, error type and claim value object of the Voice live-run service."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, NoReturn

from agent.db_models import VoiceLiveRunDB
from agent.repositories.voice_live_runs import VoiceLiveSegmentReservation

MAX_RUN_SECONDS = 28_800
MIN_SEGMENT_SECONDS = 60
MAX_SEGMENT_SECONDS = 120
MAX_OVERLAP_MILLISECONDS = 5_000
FINALIZATION_GRACE_SECONDS = 3_600
MAX_TIMELINE_ITEMS = 600


class VoiceLiveRunError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        *,
        retriable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retriable = retriable


@dataclass(frozen=True)
class VoiceLiveSegmentClaim:
    run: VoiceLiveRunDB
    reservation: VoiceLiveSegmentReservation
    idempotency_key_digest: str
    effective_idempotency_key: str


def bounded_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise VoiceLiveRunError(
            f"voice_live_run.invalid_{field}",
            f"{field} must be an integer",
            422,
        ) from exc
    if normalized < minimum or normalized > maximum:
        raise VoiceLiveRunError(
            f"voice_live_run.invalid_{field}",
            f"{field} must be between {minimum} and {maximum}",
            422,
        )
    return normalized


def maximum_sequence(run: VoiceLiveRunDB) -> int:
    advance_ms = run.segment_duration_seconds * 1000 - run.overlap_milliseconds
    return max(0, math.ceil(run.max_duration_seconds * 1000 / advance_ms) - 1)


def raise_not_found() -> NoReturn:
    raise VoiceLiveRunError(
        "voice_live_run.not_found",
        "voice live run not found",
        404,
    )


def segment_conflict(message: str) -> VoiceLiveRunError:
    return VoiceLiveRunError(
        "voice_live_run.segment_conflict",
        message,
        409,
    )


__all__ = [
    "FINALIZATION_GRACE_SECONDS",
    "MAX_OVERLAP_MILLISECONDS",
    "MAX_RUN_SECONDS",
    "MAX_SEGMENT_SECONDS",
    "MAX_TIMELINE_ITEMS",
    "MIN_SEGMENT_SECONDS",
    "VoiceLiveRunError",
    "VoiceLiveSegmentClaim",
    "bounded_int",
    "maximum_sequence",
    "raise_not_found",
    "segment_conflict",
]
