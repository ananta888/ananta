from __future__ import annotations

import threading

import pytest

from voice_runtime.backends.base import ChatResult, TranscriptionResult
from voice_runtime.backends.router import RoutedVoiceBackend, _BackendEntry
from voice_runtime.errors import BackendUnavailableError, InvalidAudioError


class _Backend:
    def __init__(self, backend_id: str, *, fail_first: bool = False, block_recovery: bool = False) -> None:
        self.backend_id = backend_id
        self.fail_first = fail_first
        self.block_recovery = block_recovery
        self.calls = 0
        self.recovery_started = threading.Event()
        self.release_recovery = threading.Event()

    def name(self) -> str:
        return self.backend_id

    def list_models(self) -> list[dict]:
        return []

    def context_capabilities(self) -> frozenset[str]:
        return frozenset()

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise BackendUnavailableError("backend unavailable")
        if self.block_recovery and self.calls == 2:
            self.recovery_started.set()
            assert self.release_recovery.wait(timeout=2)
        return TranscriptionResult(text=self.backend_id, raw_backend=self.backend_id)

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        return ChatResult(text=self.backend_id)


class _InvalidBackend(_Backend):
    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        self.calls += 1
        raise InvalidAudioError("invalid audio")


def test_fallback_trace_is_redacted_and_uses_stable_reason_codes() -> None:
    primary = _Backend("primary", fail_first=True)
    fallback = _Backend("fallback")
    router = RoutedVoiceBackend(
        [_BackendEntry("primary", primary), _BackendEntry("fallback", fallback)],
        failure_threshold=1,
        cooldown_seconds=30,
        clock=lambda: 0.0,
    )

    result = router.transcribe(filename="audio.wav", content=b"safe")

    assert result.text == "fallback"
    assert result.decision_trace["fallback_attempts"] == [
        {"backend": "primary", "status": "failed", "reason_code": "unavailable"},
        {"backend": "fallback", "status": "succeeded", "reason_code": "ok"},
    ]
    assert "backend unavailable" not in str(result.decision_trace)


def test_invalid_input_never_starts_a_fallback_backend() -> None:
    primary = _InvalidBackend("primary")
    fallback = _Backend("fallback")
    router = RoutedVoiceBackend([_BackendEntry("primary", primary), _BackendEntry("fallback", fallback)])

    with pytest.raises(InvalidAudioError):
        router.transcribe(filename="audio.wav", content=b"invalid")

    assert primary.calls == 1
    assert fallback.calls == 0


def test_circuit_breaker_allows_only_one_half_open_probe() -> None:
    now = [0.0]
    primary = _Backend("primary", fail_first=True, block_recovery=True)
    fallback = _Backend("fallback")
    router = RoutedVoiceBackend(
        [_BackendEntry("primary", primary), _BackendEntry("fallback", fallback)],
        failure_threshold=1,
        cooldown_seconds=10,
        clock=lambda: now[0],
    )
    assert router.transcribe(filename="audio.wav", content=b"first").text == "fallback"
    assert router.transcribe(filename="audio.wav", content=b"cooldown").text == "fallback"

    now[0] = 11.0
    probe_result: list[TranscriptionResult] = []
    probe = threading.Thread(
        target=lambda: probe_result.append(router.transcribe(filename="audio.wav", content=b"probe")),
        daemon=True,
    )
    probe.start()
    assert primary.recovery_started.wait(timeout=2)

    concurrent = router.transcribe(filename="audio.wav", content=b"concurrent")
    primary.release_recovery.set()
    probe.join(timeout=2)

    assert concurrent.text == "fallback"
    assert concurrent.decision_trace["fallback_attempts"][0]["reason_code"] == "circuit_open"
    assert [item.text for item in probe_result] == ["primary"]
    assert primary.calls == 2
