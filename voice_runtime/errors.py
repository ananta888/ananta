from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceRuntimeError(Exception):
    code: str
    message: str
    retriable: bool = False

    def __str__(self) -> str:
        return self.message


class InvalidAudioError(VoiceRuntimeError):
    def __init__(self, message: str):
        super().__init__("invalid_input", message, False)


class PolicyBlockedError(VoiceRuntimeError):
    def __init__(self, message: str):
        super().__init__("policy_blocked", message, False)


class BackendUnavailableError(VoiceRuntimeError):
    def __init__(self, message: str):
        super().__init__("unavailable", message, True)


class BackendTimeoutError(VoiceRuntimeError):
    def __init__(self, message: str):
        super().__init__("timeout", message, True)


class BackendCancelledError(VoiceRuntimeError):
    def __init__(self, message: str):
        super().__init__("cancelled", message, False)


class ResourceExhaustedError(VoiceRuntimeError):
    def __init__(self, message: str):
        super().__init__("resource_exhausted", message, True)


class BackendModelError(VoiceRuntimeError):
    def __init__(self, message: str):
        super().__init__("model_error", message, False)


FALLBACK_ERROR_CODES = frozenset({"unavailable", "timeout", "resource_exhausted"})


def normalize_backend_exception(exc: Exception) -> VoiceRuntimeError:
    if isinstance(exc, VoiceRuntimeError):
        return exc
    if isinstance(exc, TimeoutError):
        return BackendTimeoutError("voice backend timed out")
    if isinstance(exc, CancelledError):
        return BackendCancelledError("voice backend was cancelled")
    if isinstance(exc, (ValueError, TypeError)):
        return InvalidAudioError("voice backend rejected the input")
    message = str(exc).lower()
    if any(token in message for token in ("unavailable", "not configured", "not found", "missing")):
        return BackendUnavailableError("voice backend is unavailable")
    return BackendModelError("voice backend failed")
