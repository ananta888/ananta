from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_AUDIO_EXTENSIONS: tuple[str, ...] = (".wav", ".mp3", ".m4a", ".webm", ".ogg")


@dataclass(frozen=True)
class NormalizedAudio:
    filename: str
    payload: bytes
    media_type: str
    normalization_applied: bool


def normalize_audio_payload(*, filename: str, payload: bytes, media_type: str | None = None) -> NormalizedAudio:
    # MVP normalization: validate supported containers and keep byte payload unchanged.
    lower_filename = (filename or "audio").lower()
    if not any(lower_filename.endswith(ext) for ext in SUPPORTED_AUDIO_EXTENSIONS):
        # Allow unknown extension with fallback media type, but tag normalization state.
        return NormalizedAudio(
            filename=filename or "audio",
            payload=payload,
            media_type=media_type or "application/octet-stream",
            normalization_applied=False,
        )
    return NormalizedAudio(
        filename=filename or "audio",
        payload=payload,
        media_type=media_type or "audio/auto",
        normalization_applied=True,
    )
