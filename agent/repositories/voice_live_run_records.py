"""Value types, lease bounds and errors of the Voice live-run persistence port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlmodel import Session

from agent.db_models import VoiceLiveRunDB, VoiceLiveRunSegmentDB

SEGMENT_PROCESSING_LEASE_SECONDS = 600
CORRECTION_PROCESSING_LEASE_SECONDS = 300

SessionFactory = Callable[[], Session]


class VoiceLiveRunRepositoryConflict(RuntimeError):
    pass


class VoiceLiveRunRepositoryInProgress(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceLiveSegmentReservation:
    segment: VoiceLiveRunSegmentDB
    replayed: bool


@dataclass(frozen=True)
class VoiceLiveCorrectionClaim:
    run: VoiceLiveRunDB
    segment: VoiceLiveRunSegmentDB
    claimed: bool


__all__ = [
    "CORRECTION_PROCESSING_LEASE_SECONDS",
    "SEGMENT_PROCESSING_LEASE_SECONDS",
    "SessionFactory",
    "VoiceLiveCorrectionClaim",
    "VoiceLiveRunRepositoryConflict",
    "VoiceLiveRunRepositoryInProgress",
    "VoiceLiveSegmentReservation",
]
