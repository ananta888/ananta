"""Provider-neutral, content-free observation port for model attempts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class ModelInvocationAttemptObservation:
    profile_id: str | None
    provider_id: str
    model_id: str
    success: bool
    reason_code: str
    call_profile: Mapping[str, Any] | None
    fallback_index: int
    context_capacity: int
    confidence_available: bool
    goal_id: str | None
    task_id: str | None


class ModelInvocationObservationPort(Protocol):
    def observe_attempt(self, observation: ModelInvocationAttemptObservation) -> None: ...


__all__ = ["ModelInvocationAttemptObservation", "ModelInvocationObservationPort"]
