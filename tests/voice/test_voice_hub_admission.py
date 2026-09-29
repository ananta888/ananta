from __future__ import annotations

import io
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.services.voice_admission_service import (
    VoiceAdmissionLimits,
    VoiceAdmissionService,
    estimate_batch_audio_seconds,
    reserve_stream_audio_seconds,
)
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal
from agent.services.voice_idempotency_service import VoiceIdempotencyService


def _principal(subject: str = "user-a") -> VoicePrincipal:
    return VoicePrincipal(tenant_id="tenant-a", subject=subject)


def _deadline(seconds: float = 2.0) -> int:
    return time.time_ns() // 1_000_000 + round(seconds * 1000)


def _wav(duration_seconds: float) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16_000)
        destination.writeframes(b"\x00\x00" * round(16_000 * duration_seconds))
    return output.getvalue()


def test_batch_audio_admission_uses_exact_wav_duration_and_fail_closed_fallback() -> None:
    assert estimate_batch_audio_seconds(
        filename="sample.wav",
        content=_wav(0.25),
        unknown_audio_seconds=30,
    ) == pytest.approx(0.25)
    assert estimate_batch_audio_seconds(
        filename="sample.webm",
        content=b"opaque-compressed-audio",
        unknown_audio_seconds=30,
    ) == 30


def test_stream_audio_reservation_is_exact_for_pcm_and_fail_closed_for_containers() -> None:
    assert reserve_stream_audio_seconds(
        media_type="audio/pcm;rate=16000;channels=1",
        requested_audio_seconds=0.25,
        max_audio_seconds=30,
    ) == pytest.approx(0.25)
    assert reserve_stream_audio_seconds(
        media_type="audio/webm",
        requested_audio_seconds=0.25,
        max_audio_seconds=30,
    ) == 30


def test_admission_waits_fifo_and_never_delegates_beyond_concurrency_budget() -> None:
    service = VoiceAdmissionService()
    limits = VoiceAdmissionLimits(
        max_concurrent_requests=1,
        max_queue_depth=1,
        max_inflight_audio_seconds=10,
        max_audio_seconds_per_request=10,
    )
    first = service.acquire(_principal(), audio_seconds=2, deadline_epoch_ms=_deadline(), limits=limits)
    acquired: list[str] = []

    worker = threading.Thread(
        target=lambda: acquired.append(
            service.acquire(
                _principal("user-b"),
                audio_seconds=2,
                deadline_epoch_ms=_deadline(),
                limits=limits,
            ).lease_id
        )
    )
    worker.start()
    for _index in range(50):
        if service.snapshot()["queued_requests"] == 1:
            break
        time.sleep(0.005)

    assert service.snapshot() == {
        "active_requests": 1,
        "queued_requests": 1,
        "inflight_audio_seconds": 2,
    }
    service.release(first)
    worker.join(timeout=1)

    assert len(acquired) == 1
    service.release(acquired[0])
    assert service.snapshot()["active_requests"] == 0


def test_admission_rejects_full_queue_and_counts_queue_time_against_deadline() -> None:
    service = VoiceAdmissionService()
    limits = VoiceAdmissionLimits(
        max_concurrent_requests=1,
        max_queue_depth=0,
        max_inflight_audio_seconds=2,
        max_audio_seconds_per_request=2,
    )
    first = service.acquire(_principal(), audio_seconds=1, deadline_epoch_ms=_deadline(), limits=limits)
    with pytest.raises(VoiceGovernanceError) as full:
        service.acquire(_principal("user-b"), audio_seconds=1, deadline_epoch_ms=_deadline(), limits=limits)
    assert full.value.code == "voice_admission.queue_full"

    queued_limits = VoiceAdmissionLimits(
        max_concurrent_requests=1,
        max_queue_depth=1,
        max_inflight_audio_seconds=2,
        max_audio_seconds_per_request=2,
    )
    with pytest.raises(VoiceGovernanceError) as expired:
        service.acquire(
            _principal("user-b"),
            audio_seconds=1,
            deadline_epoch_ms=_deadline(0.02),
            limits=queued_limits,
        )
    assert expired.value.code == "voice_admission.deadline_exceeded"
    service.release(first)


def test_stream_can_release_concurrency_without_releasing_audio_reservation() -> None:
    service = VoiceAdmissionService()
    limits = VoiceAdmissionLimits(
        max_concurrent_requests=1,
        max_queue_depth=1,
        max_inflight_audio_seconds=10,
        max_audio_seconds_per_request=10,
    )
    stream = service.acquire(_principal(), audio_seconds=4, deadline_epoch_ms=_deadline(), limits=limits)
    service.release_concurrency(stream)

    batch = service.acquire(_principal(), audio_seconds=3, deadline_epoch_ms=_deadline(), limits=limits)
    assert service.snapshot() == {
        "active_requests": 1,
        "queued_requests": 0,
        "inflight_audio_seconds": 7,
    }
    service.release(batch)
    service.release(stream)


def test_concurrent_tenant_bound_idempotency_claims_have_exactly_one_owner(client) -> None:
    del client  # Ensures the test database/application lifecycle is initialized.
    participants = 6
    barrier = threading.Barrier(participants)
    principal = VoicePrincipal(tenant_id="parallel-idempotency-tenant", subject="parallel-user")

    def claim() -> tuple[str, object]:
        barrier.wait(timeout=2)
        try:
            value = VoiceIdempotencyService().begin(
                principal,
                operation="voice.transcribe",
                idempotency_key="parallel-voice-request",
                payload={"audio_sha256": "a" * 64, "effective_configuration": {"version": 1}},
            )
            return "owned", value
        except VoiceGovernanceError as error:
            return error.code, error

    with ThreadPoolExecutor(max_workers=participants) as executor:
        outcomes = list(executor.map(lambda _index: claim(), range(participants)))

    owners = [value for status, value in outcomes if status == "owned"]
    assert len(owners) == 1
    assert [status for status, _value in outcomes].count("voice_governance.operation_in_progress") == participants - 1
    VoiceIdempotencyService().abandon(owners[0])
