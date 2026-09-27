"""The decision provider port (DPRV).

Every provider -- TypeSafe Jev, the local llama.cpp decision server, an LLM
prompt, rules, a mock -- implements this small interface (ISP: only what a
caller needs). ``adecide`` runs the blocking call off the event loop, so async
callers need no second implementation.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from agent.services.decision_providers.types import DecisionRequest, DecisionResult


@runtime_checkable
class DecisionProvider(Protocol):
    provider_id: str

    def is_available(self) -> tuple[bool, str]:
        """``(True, "ok")`` or ``(False, reason_code)`` without doing a decision."""
        ...

    def decide(self, request: DecisionRequest, *, timeout_seconds: float | None = None) -> DecisionResult:
        """A validated result for every question of ``request``, or ``DecisionProviderError``."""
        ...


async def adecide(provider: DecisionProvider, request: DecisionRequest, *,
                  timeout_seconds: float | None = None) -> DecisionResult:
    return await asyncio.to_thread(provider.decide, request, timeout_seconds=timeout_seconds)
