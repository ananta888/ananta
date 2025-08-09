from dataclasses import dataclass, field
from typing import List

from ..agents.base import Agent


@dataclass
class ControllerAgent(Agent):
    """Agent subclass that keeps a simple blacklist and log."""

    blacklist: List[str] = field(default_factory=list)
    log: List[str] = field(default_factory=list)

    def add_task(self, task: str) -> None:
        if task in self.blacklist:
            self.log.append(f"Task '{task}' is blacklisted")
            return
        self.tasks.append(task)
        self.log.append(f"Added task: {task}")
