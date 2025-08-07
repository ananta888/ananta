"""Controller agent managing tasks and blacklist."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set

try:  # pragma: no cover - import fallbacks
    from ..agents.base import Agent
except Exception:  # If imported as top-level ``controller`` package
    from src.agents.base import Agent  # type: ignore


@dataclass
class ControllerAgent(Agent):
    """Agent responsible for distributing tasks and tracking a blacklist."""

    tasks: List[str] = field(default_factory=list)
    blacklist: Set[str] = field(default_factory=set)
    _log: List[str] = field(default_factory=list)

    def assign_task(self) -> Optional[str]:
        """Return the next non-blacklisted task and log the assignment."""

        while self.tasks:
            task = self.tasks.pop(0)
            if task in self.blacklist:
                continue
            self._log.append(f"assigned:{task}")
            return task
        return None

    def update_blacklist(self, entry: str) -> None:
        """Add ``entry`` to the blacklist and log the update."""

        self.blacklist.add(entry)
        self._log.append(f"blacklisted:{entry}")

    def log_status(self) -> List[str]:
        """Return a copy of the internal log."""

        return list(self._log)

    def clear_log(self) -> None:
        """Remove all log entries."""

        self._log.clear()
