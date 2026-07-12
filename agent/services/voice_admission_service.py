"""Hub-owned admission control for Voice runtime delegation.

The runtime has its own backend/resource envelope.  This module protects the
Hub-to-runtime boundary: a request must own a bounded queue/audio lease before
the Hub creates a delegation task or calls the runtime.
"""

from __future__ import annotations

import io
import secrets
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass, replace

from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal


@dataclass(frozen=True)
class VoiceAdmissionLimits:
    max_concurrent_requests: int = 2
    max_queue_depth: int = 16
    max_inflight_audio_seconds: float = 7_200.0
    max_audio_seconds_per_request: float = 3_600.0

    def __post_init__(self) -> None:
        if self.max_concurrent_requests < 1:
            raise ValueError("max_concurrent_requests must be positive")
        if self.max_queue_depth < 0:
            raise ValueError("max_queue_depth must not be negative")
        if self.max_inflight_audio_seconds <= 0 or self.max_audio_seconds_per_request <= 0:
            raise ValueError("Voice audio-second budgets must be positive")


@dataclass(frozen=True)
class VoiceAdmissionLease:
    lease_id: str
    tenant_id: str
    owner_subject: str
    audio_seconds: float
    deadline_epoch_ms: int
    holds_concurrency: bool = True


@dataclass(frozen=True)
class _QueuedRequest:
    ticket_id: str
    tenant_id: str
    owner_subject: str
    audio_seconds: float
    deadline_epoch_ms: int


class VoiceAdmissionService:
    """FIFO admission with bounded queue and in-flight audio accounting."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._queue: deque[_QueuedRequest] = deque()
        self._leases: dict[str, VoiceAdmissionLease] = {}

    def acquire(
        self,
        principal: VoicePrincipal,
        *,
        audio_seconds: float,
        deadline_epoch_ms: int,
        limits: VoiceAdmissionLimits,
    ) -> VoiceAdmissionLease:
        requested_audio = max(0.001, float(audio_seconds))
        if requested_audio > limits.max_audio_seconds_per_request:
            raise VoiceGovernanceError(
                code="voice_admission.audio_budget_exceeded",
                message="voice request exceeds its audio-second budget",
                status_code=413,
            )
        if requested_audio > limits.max_inflight_audio_seconds:
            raise VoiceGovernanceError(
                code="voice_admission.audio_capacity_exceeded",
                message="voice request exceeds Hub in-flight audio capacity",
                status_code=429,
            )
        queued = _QueuedRequest(
            ticket_id=f"voice-ticket-{secrets.token_urlsafe(12)}",
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            audio_seconds=requested_audio,
            deadline_epoch_ms=int(deadline_epoch_ms),
        )
        with self._condition:
            self._purge_expired_locked()
            if len(self._queue) >= limits.max_queue_depth and not self._can_start_locked(queued, limits):
                raise VoiceGovernanceError(
                    code="voice_admission.queue_full",
                    message="Hub voice delegation queue is full",
                    status_code=429,
                )
            self._queue.append(queued)
            try:
                while True:
                    self._purge_expired_locked()
                    remaining = (queued.deadline_epoch_ms - _epoch_ms()) / 1000.0
                    if remaining <= 0:
                        raise VoiceGovernanceError(
                            code="voice_admission.deadline_exceeded",
                            message="voice request deadline expired while queued",
                            status_code=504,
                        )
                    if self._queue and self._queue[0].ticket_id == queued.ticket_id:
                        if self._can_start_locked(queued, limits):
                            self._queue.popleft()
                            lease = VoiceAdmissionLease(
                                lease_id=f"voice-lease-{secrets.token_urlsafe(18)}",
                                tenant_id=queued.tenant_id,
                                owner_subject=queued.owner_subject,
                                audio_seconds=queued.audio_seconds,
                                deadline_epoch_ms=queued.deadline_epoch_ms,
                            )
                            self._leases[lease.lease_id] = lease
                            self._condition.notify_all()
                            return lease
                    self._condition.wait(timeout=min(remaining, 0.25))
            except Exception:
                self._remove_ticket_locked(queued.ticket_id)
                self._condition.notify_all()
                raise

    def release(self, lease: VoiceAdmissionLease | str | None) -> None:
        lease_id = lease.lease_id if isinstance(lease, VoiceAdmissionLease) else str(lease or "")
        if not lease_id:
            return
        with self._condition:
            self._leases.pop(lease_id, None)
            self._condition.notify_all()

    def release_concurrency(self, lease: VoiceAdmissionLease | str | None) -> None:
        """Retain a stream's audio reservation without occupying an HTTP slot."""

        lease_id = lease.lease_id if isinstance(lease, VoiceAdmissionLease) else str(lease or "")
        if not lease_id:
            return
        with self._condition:
            current = self._leases.get(lease_id)
            if current is not None and current.holds_concurrency:
                self._leases[lease_id] = replace(current, holds_concurrency=False)
                self._condition.notify_all()

    def snapshot(self) -> dict[str, float | int]:
        """Return aggregate counters only; principals never become metric labels."""

        with self._condition:
            self._purge_expired_locked()
            return {
                "active_requests": sum(item.holds_concurrency for item in self._leases.values()),
                "queued_requests": len(self._queue),
                "inflight_audio_seconds": sum(item.audio_seconds for item in self._leases.values()),
            }

    def _can_start_locked(self, queued: _QueuedRequest, limits: VoiceAdmissionLimits) -> bool:
        if sum(item.holds_concurrency for item in self._leases.values()) >= limits.max_concurrent_requests:
            return False
        active_audio = sum(item.audio_seconds for item in self._leases.values())
        return active_audio + queued.audio_seconds <= limits.max_inflight_audio_seconds

    def _purge_expired_locked(self) -> None:
        now = _epoch_ms()
        expired_leases = [key for key, value in self._leases.items() if value.deadline_epoch_ms <= now]
        for lease_id in expired_leases:
            self._leases.pop(lease_id, None)
        if self._queue:
            self._queue = deque(item for item in self._queue if item.deadline_epoch_ms > now)

    def _remove_ticket_locked(self, ticket_id: str) -> None:
        self._queue = deque(item for item in self._queue if item.ticket_id != ticket_id)


def estimate_batch_audio_seconds(
    *,
    filename: str,
    content: bytes,
    unknown_audio_seconds: float,
) -> float:
    """Return exact WAV/PCM duration or a fail-closed bound for compressed input."""

    bounded_unknown = max(0.001, float(unknown_audio_seconds))
    normalized_filename = str(filename or "").lower()
    if normalized_filename.endswith((".pcm", ".s16", ".s16le")):
        return min(bounded_unknown, max(0.001, len(content) / (16_000 * 2)))
    try:
        with wave.open(io.BytesIO(content), "rb") as source:
            frame_rate = source.getframerate()
            if frame_rate <= 0:
                return bounded_unknown
            return min(bounded_unknown, max(0.001, source.getnframes() / frame_rate))
    except (EOFError, wave.Error):
        # The Hub deliberately does not decode compressed audio.  Reserving the
        # configured request maximum avoids under-accounting before delegation.
        return bounded_unknown


def reserve_stream_audio_seconds(
    *,
    media_type: str,
    requested_audio_seconds: float,
    max_audio_seconds: float,
) -> float:
    """Return an enforceable stream reservation.

    Raw 16 kHz mono PCM has an exact byte-to-duration relationship. Container
    formats do not, so the Hub reserves the configured request maximum instead
    of trusting a client-supplied duration that cannot be verified per chunk.
    """

    requested = max(0.001, min(float(requested_audio_seconds), float(max_audio_seconds)))
    if media_type == "audio/pcm;rate=16000;channels=1":
        return requested
    return float(max_audio_seconds)


def _epoch_ms() -> int:
    return time.time_ns() // 1_000_000


voice_admission_service = VoiceAdmissionService()


def get_voice_admission_service() -> VoiceAdmissionService:
    return voice_admission_service
