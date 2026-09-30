"""Port through which the legacy Team repository delegates guarded deletion."""

from __future__ import annotations

from typing import Protocol


class LegacyTeamDeletionPort(Protocol):
    def delete_team(self, team_id: str) -> bool:
        """Delete ``team_id`` under the Hub guard; ``False`` when refused."""
        ...


__all__ = ["LegacyTeamDeletionPort"]
