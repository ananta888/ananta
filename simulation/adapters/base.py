"""SimulationModelAdapter interface (SIM-015)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from simulation.models.action import ActionProposal


@dataclass
class AdapterResponse:
    raw_text: str
    proposal: ActionProposal | None
    parse_error: str | None = None
    tokens_used: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    model_id: str = ""

    @property
    def ok(self) -> bool:
        return self.proposal is not None and self.parse_error is None


class SimulationModelAdapter(ABC):
    """Protocol for agent decision-making backends.

    Implementations must be stateless between calls; all context arrives
    in `messages`. No LLM calls happen at import time.
    """

    @abstractmethod
    def generate(self, messages: list[dict[str, str]],
                  agent_id: str, **kwargs: Any) -> AdapterResponse:
        """Send messages → return parsed ActionProposal or fallback noop."""

    @property
    @abstractmethod
    def provider(self) -> str:
        """Identifier like 'dummy', 'ollama', 'openrouter'."""

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Fully qualified model identifier."""
