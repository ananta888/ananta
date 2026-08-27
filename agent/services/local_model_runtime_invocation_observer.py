"""Content-free observability for calls routed to local GPU providers."""

from __future__ import annotations

import threading
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any, Callable, Mapping

from agent.services.model_invocation_observation import (
    ModelInvocationAttemptObservation,
)
from ananta_contracts.local_model_runtime import (
    LocalRuntimeInvocationObservation,
    LocalRuntimeSnapshot,
)


class LocalRuntimeInvocationObserver:
    """Records a bounded read model and emits the same closed facts to audit."""

    _RUNTIMES_BY_PROFILE = {
        "local_kat_coder_v25_heavy": "kat",
        "local_lfm25_agentic_fast": "lfm",
    }

    def __init__(
        self,
        *,
        snapshot: Callable[[], LocalRuntimeSnapshot],
        audit_sink: Callable[[str, Mapping[str, object]], None],
        capacity: int = 500,
    ) -> None:
        if capacity < 1 or capacity > 10_000:
            raise ValueError("local_runtime_invocation_capacity_invalid")
        self._snapshot = snapshot
        self._audit_sink = audit_sink
        self._items: deque[LocalRuntimeInvocationObservation] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    @classmethod
    def supports(cls, profile_id: str | None) -> bool:
        return str(profile_id or "").strip().lower() in cls._RUNTIMES_BY_PROFILE

    def observe(
        self,
        *,
        profile_id: str,
        provider_id: str,
        model_id: str,
        success: bool,
        reason_code: str,
        call_profile: Mapping[str, Any] | None,
        fallback_index: int,
        context_capacity: int,
        confidence_available: bool,
        goal_id: str | None,
        task_id: str | None,
    ) -> LocalRuntimeInvocationObservation:
        normalized_profile = str(profile_id).strip().lower()
        runtime_id = self._RUNTIMES_BY_PROFILE.get(normalized_profile)
        if runtime_id is None:
            raise ValueError("local_runtime_invocation_profile_unsupported")
        snapshot = self._snapshot()
        status = next(item for item in snapshot.runtimes if item.runtime_id == runtime_id)
        profile = dict(call_profile or {})
        prompt_tokens = _non_negative_int(profile.get("prompt_tokens"))
        completion_tokens = _non_negative_int(profile.get("completion_tokens"))
        total_tokens = _non_negative_int(profile.get("total_tokens"))
        if not total_tokens:
            total_tokens = prompt_tokens + completion_tokens
        observation = LocalRuntimeInvocationObservation(
            invocation_id=f"inv-{uuid.uuid4().hex}",
            observed_at=_now(),
            runtime_id=runtime_id,
            provider_id=provider_id,
            model_id=model_id,
            profile_id=normalized_profile,
            goal_id=goal_id,
            task_id=task_id,
            success=success,
            reason_code=reason_code,
            latency_ms=_non_negative_int(profile.get("latency_ms")),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            context_capacity=context_capacity,
            fallback_index=fallback_index,
            confidence_available=confidence_available,
            readiness=status.readiness,
            resource_reason_code=status.reason_code,
            free_vram_bytes=snapshot.free_vram_bytes,
            available_ram_bytes=snapshot.available_ram_bytes,
        )
        with self._lock:
            self._items.append(observation)
        self._audit_sink("local_model_runtime_invocation", observation.to_wire())
        return observation

    def observe_attempt(self, observation: ModelInvocationAttemptObservation) -> None:
        if not self.supports(observation.profile_id):
            return
        self.observe(
            profile_id=str(observation.profile_id),
            provider_id=observation.provider_id,
            model_id=observation.model_id,
            success=observation.success,
            reason_code=observation.reason_code,
            call_profile=observation.call_profile,
            fallback_index=observation.fallback_index,
            context_capacity=observation.context_capacity,
            confidence_available=observation.confidence_available,
            goal_id=observation.goal_id,
            task_id=observation.task_id,
        )

    def emit(self, event: Mapping[str, Any]) -> None:
        """Consume the generic Tiny Router telemetry contract as a sink."""

        attempts = event.get("attempts")
        if not isinstance(attempts, list):
            return
        attempt = next(
            (
                item
                for item in attempts
                if isinstance(item, Mapping) and str(item.get("profile_id") or "").strip().lower() == "needle-2-45m"
            ),
            None,
        )
        if attempt is None:
            return
        snapshot = self._snapshot()
        status = next(item for item in snapshot.runtimes if item.runtime_id == "needle")
        confidence = event.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            confidence = None
        observation = LocalRuntimeInvocationObservation(
            invocation_id=f"inv-{uuid.uuid4().hex}",
            observed_at=_now(),
            runtime_id="needle",
            provider_id="needle-sidecar",
            model_id="needle-2-45m",
            profile_id="needle-2-45m",
            success=str(attempt.get("status") or "") in {"valid", "candidate", "shadow_candidate"},
            reason_code=str(attempt.get("reason_code") or event.get("reason_code") or "candidate_failed"),
            latency_ms=_non_negative_int(attempt.get("latency_ms")),
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            context_capacity=status.context_capacity,
            fallback_index=attempts.index(attempt),
            confidence_available=confidence is not None,
            candidate_only=True,
            candidate_status=str(attempt.get("status") or "unknown"),
            candidate_confidence=float(confidence) if confidence is not None else None,
            prompt_chars=_non_negative_int(event.get("prompt_chars")),
            readiness=status.readiness,
            resource_reason_code=status.reason_code,
            free_vram_bytes=snapshot.free_vram_bytes,
            available_ram_bytes=snapshot.available_ram_bytes,
        )
        with self._lock:
            self._items.append(observation)
        self._audit_sink("local_model_runtime_invocation", observation.to_wire())

    def read(self, *, limit: int = 100) -> tuple[LocalRuntimeInvocationObservation, ...]:
        bounded = max(1, min(int(limit), 500))
        with self._lock:
            return tuple(reversed(tuple(self._items)[-bounded:]))


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, bytes, bytearray)):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["LocalRuntimeInvocationObserver"]
