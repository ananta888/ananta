"""Persistence port for atomic SFU broadcast user-intent mutations."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from agent.models.sfu_broadcast_command import (
    SfuBroadcastCommandMutation,
    SfuBroadcastCommandMutationResult,
)


class SfuBroadcastCommandRepositoryPort(Protocol):
    def execute(
        self, mutation: SfuBroadcastCommandMutation
    ) -> SfuBroadcastCommandMutationResult: ...

    def purge_expired(self, *, now: datetime | None = None) -> int: ...


__all__ = [
    "SfuBroadcastCommandRepositoryPort",
]
