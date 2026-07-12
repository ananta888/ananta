from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

SUPPORTED_AUDIO_EXTENSIONS: tuple[str, ...] = (".wav", ".mp3", ".m4a", ".webm", ".ogg")

_EXTENSION_FORMATS = {
    ".wav": "wav",
    ".mp3": "mp3",
    ".m4a": "m4a",
    ".webm": "webm",
    ".ogg": "ogg",
}
_FORMAT_MEDIA_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "webm": "audio/webm",
    "ogg": "audio/ogg",
}
_MEDIA_TYPE_FORMATS = {media_type: audio_format for audio_format, media_type in _FORMAT_MEDIA_TYPES.items()}


class AudioInputError(ValueError):
    """A stable, content-free validation failure for untrusted audio."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AudioPayloadLimits:
    max_encoded_bytes: int = 25 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.max_encoded_bytes <= 0:
            raise ValueError("max_encoded_bytes must be positive")


@dataclass(frozen=True)
class NormalizedAudio:
    filename: str
    payload: bytes
    media_type: str
    normalization_applied: bool
    detected_format: str | None = None
    warnings: tuple[str, ...] = ()


def sanitize_audio_filename(filename: str | None, *, fallback: str = "audio") -> str:
    """Return a basename safe to use inside an already isolated workspace."""

    raw = str(filename or fallback).replace("\\", "/").replace("\x00", "")
    basename = PurePosixPath(raw).name
    cleaned = "".join(character for character in basename if character.isprintable()).strip()
    if cleaned in {"", ".", ".."}:
        return fallback
    cleaned = cleaned[:255]
    while len(cleaned.encode("utf-8")) > 240:
        cleaned = cleaned[:-1]
    return cleaned or fallback


def detect_audio_format(payload: bytes) -> str | None:
    """Detect supported containers from conservative file signatures."""

    if len(payload) >= 12 and payload[:4] in {b"RIFF", b"RF64"} and payload[8:12] == b"WAVE":
        return "wav"
    if payload.startswith(b"ID3") or (len(payload) >= 2 and payload[0] == 0xFF and payload[1] & 0xE0 == 0xE0):
        return "mp3"
    if payload.startswith(b"OggS"):
        return "ogg"
    if payload.startswith(b"\x1aE\xdf\xa3"):
        return "webm"
    if len(payload) >= 12 and payload[4:8] == b"ftyp":
        return "m4a"
    return None


def normalize_audio_payload(
    *,
    filename: str,
    payload: bytes,
    media_type: str | None = None,
    limits: AudioPayloadLimits | None = None,
    strict: bool = False,
) -> NormalizedAudio:
    """Validate encoded input and derive trustworthy container metadata.

    ``strict=False`` preserves the legacy passthrough used by older adapters.
    Security-sensitive decode paths opt into strict signature validation.
    """

    effective_limits = limits or AudioPayloadLimits()
    if not payload:
        raise AudioInputError("validation.empty_audio", "audio payload must not be empty")
    if len(payload) > effective_limits.max_encoded_bytes:
        raise AudioInputError("validation.audio_too_large", "encoded audio exceeds the configured byte limit")

    safe_filename = sanitize_audio_filename(filename)
    suffix = PurePosixPath(safe_filename.lower()).suffix
    extension_format = _EXTENSION_FORMATS.get(suffix)
    detected_format = detect_audio_format(payload)
    warnings: list[str] = []

    if strict and detected_format is None:
        raise AudioInputError("validation.unsupported_audio", "audio container signature is unsupported")
    if extension_format and detected_format and extension_format != detected_format:
        if strict:
            raise AudioInputError("validation.audio_format_mismatch", "audio extension does not match its content")
        warnings.append("audio_format_mismatch")
    elif extension_format and detected_format is None:
        warnings.append("audio_signature_unknown")

    effective_format = detected_format or extension_format
    supplied_media_type = str(media_type or "").split(";", 1)[0].strip().lower()
    supplied_format = _MEDIA_TYPE_FORMATS.get(supplied_media_type)
    if strict and supplied_format and detected_format and supplied_format != detected_format:
        raise AudioInputError("validation.audio_media_type_mismatch", "audio media type does not match its content")
    effective_media_type = (
        _FORMAT_MEDIA_TYPES.get(detected_format or "")
        or media_type
        or _FORMAT_MEDIA_TYPES.get(effective_format or "")
        or "application/octet-stream"
    )
    return NormalizedAudio(
        filename=safe_filename,
        payload=bytes(payload),
        media_type=effective_media_type,
        normalization_applied=extension_format is not None,
        detected_format=detected_format,
        warnings=tuple(warnings),
    )
