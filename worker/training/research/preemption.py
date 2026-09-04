"""Signal-safe preemption flag and typed checkpoint handoff."""

from __future__ import annotations

import signal
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class ResearchTrainingPreempted(RuntimeError):
    def __init__(self, optimizer_step: int) -> None:
        self.optimizer_step = optimizer_step
        super().__init__("research_training_preempted")


@dataclass(frozen=True, slots=True)
class ResearchStagePreempted(RuntimeError):
    checkpoint_receipt: dict[str, Any]


class PreemptionController:
    def __init__(self) -> None:
        self._requested = False

    @property
    def requested(self) -> bool:
        return self._requested

    def request(self) -> None:
        self._requested = True

    def install(self) -> Callable[[], None]:
        previous = {
            signum: signal.getsignal(signum)
            for signum in (signal.SIGTERM, signal.SIGUSR1)
        }

        def handler(_signum, _frame) -> None:
            self.request()

        for signum in previous:
            signal.signal(signum, handler)

        def restore() -> None:
            for signum, callback in previous.items():
                signal.signal(signum, callback)

        return restore


__all__ = ["PreemptionController", "ResearchStagePreempted", "ResearchTrainingPreempted"]
