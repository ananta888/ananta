"""Input validation of live-run capture configuration and uploaded segments."""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

from agent.db_models import VoiceLiveRunDB
from agent.services.voice_governance_domain import validate_identifier, validate_text
from agent.services.voice_live_run_contracts import (
    MAX_OVERLAP_MILLISECONDS,
    MAX_RUN_SECONDS,
    MAX_SEGMENT_SECONDS,
    MIN_SEGMENT_SECONDS,
    VoiceLiveRunError,
    bounded_int,
    maximum_sequence,
)


def validate_segment_metadata(
    run: VoiceLiveRunDB,
    *,
    sequence: int,
    started_at_ms: int,
    ended_at_ms: int,
    duration_ms: int,
    overlap_milliseconds: int,
) -> dict[str, int]:
    normalized_sequence = bounded_int(
        sequence,
        field="sequence",
        minimum=0,
        maximum=maximum_sequence(run),
    )
    started = bounded_int(
        started_at_ms,
        field="started_at_ms",
        minimum=0,
        maximum=run.max_duration_seconds * 1000,
    )
    ended = bounded_int(
        ended_at_ms,
        field="ended_at_ms",
        minimum=1,
        maximum=run.max_duration_seconds * 1000,
    )
    duration = bounded_int(
        duration_ms,
        field="duration_ms",
        minimum=1,
        maximum=run.segment_duration_seconds * 1000,
    )
    overlap = bounded_int(
        overlap_milliseconds,
        field="overlap_milliseconds",
        minimum=0,
        maximum=run.overlap_milliseconds,
    )
    if ended <= started or ended - started > run.segment_duration_seconds * 1000:
        raise VoiceLiveRunError(
            "voice_live_run.invalid_segment_timeline",
            "segment timeline is invalid or exceeds the configured duration",
            422,
        )
    if abs(duration - (ended - started)) > 500:
        raise VoiceLiveRunError(
            "voice_live_run.invalid_segment_duration",
            "duration_ms must match the segment timeline",
            422,
        )
    return {
        "sequence": normalized_sequence,
        "started_at_ms": started,
        "ended_at_ms": ended,
        "duration_ms": duration,
        "overlap_milliseconds": overlap,
    }


def validate_audio_duration(audio: bytes, declared_duration_ms: int) -> None:
    """Validate the supported WAV/PCM segment against its timeline metadata."""

    if not audio:
        raise VoiceLiveRunError(
            "voice_live_run.empty_audio",
            "segment audio must not be empty",
            422,
        )
    actual_duration_ms: float
    if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(audio), "rb") as source:
                if source.getnchannels() != 1 or source.getsampwidth() != 2:
                    raise VoiceLiveRunError(
                        "voice_live_run.invalid_audio_format",
                        "WAV segments must contain mono 16-bit PCM",
                        422,
                    )
                frame_rate = source.getframerate()
                if frame_rate <= 0:
                    raise ValueError("invalid WAV frame rate")
                actual_duration_ms = source.getnframes() * 1000.0 / frame_rate
        except (EOFError, wave.Error, ValueError) as exc:
            raise VoiceLiveRunError(
                "voice_live_run.invalid_audio_format",
                "segment must be a valid PCM WAV or raw PCM16/16kHz/mono payload",
                422,
            ) from exc
    else:
        # The direct raw path is deliberately strict and matches the live
        # capture transport contract: PCM16, mono, 16 kHz.
        if len(audio) % 2:
            raise VoiceLiveRunError(
                "voice_live_run.invalid_audio_format",
                "raw PCM16 payload must contain complete samples",
                422,
            )
        actual_duration_ms = len(audio) * 1000.0 / (16_000 * 2)
    if actual_duration_ms > MAX_SEGMENT_SECONDS * 1000 + 1:
        raise VoiceLiveRunError(
            "voice_live_run.audio_too_long",
            "segment audio exceeds 120 seconds",
            422,
        )
    if abs(actual_duration_ms - declared_duration_ms) > 750:
        raise VoiceLiveRunError(
            "voice_live_run.audio_duration_mismatch",
            "segment audio duration does not match duration_ms",
            422,
        )


@dataclass(frozen=True)
class VoiceLiveRunConfiguration:
    configuration_session_id: str | None
    language: str | None
    segment_duration_seconds: int
    max_duration_seconds: int
    overlap_milliseconds: int


def validate_run_configuration(
    *,
    configuration_session_id: str | None,
    language: str | None,
    segment_duration_seconds: int,
    max_duration_seconds: int,
    overlap_milliseconds: int,
) -> VoiceLiveRunConfiguration:
    """Normalize the capture configuration of a new live run (after lease check)."""

    normalized_session_id = (
        validate_identifier(
            configuration_session_id,
            field="configuration_session_id",
            max_length=128,
        )
        if configuration_session_id
        else None
    )
    normalized_language = validate_text(
        language,
        field="language",
        max_length=32,
        required=False,
    )
    segment_seconds = bounded_int(
        segment_duration_seconds,
        field="segment_duration_seconds",
        minimum=MIN_SEGMENT_SECONDS,
        maximum=MAX_SEGMENT_SECONDS,
    )
    max_seconds = bounded_int(
        max_duration_seconds,
        field="max_duration_seconds",
        minimum=MIN_SEGMENT_SECONDS,
        maximum=MAX_RUN_SECONDS,
    )
    if max_seconds < segment_seconds:
        raise VoiceLiveRunError(
            "voice_live_run.invalid_max_duration_seconds",
            "max_duration_seconds must be at least one segment duration",
            422,
        )
    overlap_ms = bounded_int(
        overlap_milliseconds,
        field="overlap_milliseconds",
        minimum=0,
        maximum=MAX_OVERLAP_MILLISECONDS,
    )
    if overlap_ms >= segment_seconds * 1000:
        raise VoiceLiveRunError(
            "voice_live_run.invalid_overlap",
            "overlap_milliseconds must be shorter than one segment",
            422,
        )
    return VoiceLiveRunConfiguration(
        configuration_session_id=normalized_session_id,
        language=normalized_language,
        segment_duration_seconds=segment_seconds,
        max_duration_seconds=max_seconds,
        overlap_milliseconds=overlap_ms,
    )


__all__ = [
    "VoiceLiveRunConfiguration",
    "validate_audio_duration",
    "validate_run_configuration",
    "validate_segment_metadata",
]
