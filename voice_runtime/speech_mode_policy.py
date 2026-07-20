"""Deterministic semantic-speech fallback state machine."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from ananta_contracts.semantic_speech import SpeechMode

SpeechModeEventType = Literal[
    "negotiate_live",
    "negotiate_reconstruction",
    "negotiate_delayed",
    "negotiate_segment",
    "partial",
    "segment_final",
    "source_ready",
    "correction_complete",
    "correction_failed",
    "transport_lost",
    "transport_restored",
    "source_expired",
    "reconstruction_failed",
    "consent_revoked",
    "stale_epoch",
    "user_ordinary_override",
]


@dataclass(frozen=True, slots=True)
class SpeechModeEvent:
    sequence: int
    epoch: int
    event_type: SpeechModeEventType
    consent_active: bool = True
    ordinary_audio_healthy: bool = True


@dataclass(frozen=True, slots=True)
class SpeechModeState:
    mode: SpeechMode = "ordinary_audio"
    epoch: int = 1
    last_sequence: int = 0
    reason_code: str = "initial_ordinary_audio"
    live_partials: bool = False
    correct_each_segment: bool = False
    policy_version: int = 1


class SpeechModeMachine:
    def __init__(self, initial: SpeechModeState | None = None) -> None:
        self._state = initial or SpeechModeState()

    @property
    def state(self) -> SpeechModeState:
        return self._state

    def apply(self, event: SpeechModeEvent) -> SpeechModeState:
        if event.event_type in {"consent_revoked", "user_ordinary_override"}:
            reason = "consent_revoked" if event.event_type == "consent_revoked" else "user_ordinary_override"
            self._state = replace(
                self._state,
                mode="ordinary_audio" if event.ordinary_audio_healthy else "fallback",
                epoch=max(self._state.epoch, event.epoch),
                last_sequence=max(self._state.last_sequence, event.sequence),
                reason_code=reason,
                live_partials=False,
                correct_each_segment=False,
            )
            return self._state
        if event.epoch != self._state.epoch:
            if event.epoch < self._state.epoch:
                return self._state
            self._state = replace(
                self._state,
                mode="ordinary_audio" if event.ordinary_audio_healthy else "fallback",
                epoch=event.epoch,
                last_sequence=event.sequence,
                reason_code="stale_epoch",
                live_partials=False,
                correct_each_segment=False,
            )
            return self._state
        if event.sequence <= self._state.last_sequence:
            return self._state
        if not event.consent_active:
            self._state = replace(
                self._state,
                mode="ordinary_audio" if event.ordinary_audio_healthy else "fallback",
                last_sequence=event.sequence,
                reason_code="consent_missing",
                live_partials=False,
                correct_each_segment=False,
            )
            return self._state
        mode, reason, live, correction = self._transition(event)
        self._state = replace(
            self._state,
            mode=mode,
            last_sequence=event.sequence,
            reason_code=reason,
            live_partials=live,
            correct_each_segment=correction,
        )
        return self._state

    def _transition(self, event: SpeechModeEvent) -> tuple[SpeechMode, str, bool, bool]:
        if event.event_type == "negotiate_live":
            return "transcript_live", "live_negotiated", True, True
        if event.event_type == "negotiate_reconstruction":
            return "semantic_reconstruction", "reconstruction_negotiated", True, True
        if event.event_type == "negotiate_delayed":
            return "delayed_correction", "delayed_correction_negotiated", True, True
        if event.event_type == "negotiate_segment":
            return "segment_only", "segment_mode_negotiated", False, True
        safe_failures = {
            "transport_lost": "transport_lost",
            "reconstruction_failed": "reconstruction_failed",
            "stale_epoch": "stale_epoch",
        }
        if event.event_type in safe_failures:
            target: SpeechMode = "ordinary_audio" if event.ordinary_audio_healthy else "fallback"
            return target, safe_failures[event.event_type], False, False
        if event.event_type == "source_expired" and self._state.mode == "delayed_correction":
            return "transcript_live", "source_audio_expired", True, False
        if event.event_type == "correction_failed":
            return self._state.mode, "correction_failed", self._state.live_partials, True
        if event.event_type == "transport_restored":
            return "ordinary_audio", "ordinary_transport_restored", False, False
        return (
            self._state.mode,
            f"{event.event_type}_accepted",
            self._state.live_partials,
            self._state.correct_each_segment,
        )


__all__ = ["SpeechModeEvent", "SpeechModeEventType", "SpeechModeMachine", "SpeechModeState"]
