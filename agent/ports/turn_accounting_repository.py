"""Persistence port for bounded, content-free TURN accounting."""

from __future__ import annotations

from typing import Protocol

from agent.models.turn_accounting import (
    TurnAccountingIngestRequest,
    TurnAccountingPage,
    TurnAccountingRepositoryResult,
    TurnAccountingScope,
)


class TurnAccountingRepositoryPort(Protocol):
    def ingest(self, request: TurnAccountingIngestRequest) -> TurnAccountingRepositoryResult: ...

    def page(
        self,
        scope: TurnAccountingScope,
        *,
        cursor: str | None,
        limit: int,
        now: int,
    ) -> TurnAccountingPage: ...

    def purge_expired(self, *, now: int, limit: int) -> int: ...


__all__ = [
    "TurnAccountingRepositoryPort",
]
