"""Startup-frozen allowlist for speech training backends."""

from __future__ import annotations

from types import MappingProxyType
from typing import Iterable, Mapping

from ananta_contracts.speech_adaptation import SUPPORTED_BACKENDS
from worker.speech_training.backend import SpeechTrainingBackend, SpeechTrainingBackendError


class SpeechTrainingBackendRegistry:
    """Immutable registry; payloads can select names but never import code."""

    def __init__(self, backends: Iterable[SpeechTrainingBackend]) -> None:
        values: dict[str, SpeechTrainingBackend] = {}
        for backend in backends:
            name = str(getattr(backend, "name", "")).strip().casefold()
            if name not in SUPPORTED_BACKENDS:
                raise ValueError(f"speech backend {name!r} is not contract-allowlisted")
            if name in values:
                raise ValueError(f"duplicate speech backend {name!r}")
            values[name] = backend
        if not values:
            raise ValueError("at least one speech backend must be registered")
        self._backends: Mapping[str, SpeechTrainingBackend] = MappingProxyType(values)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._backends))

    def capabilities(self) -> dict[str, dict[str, str | bool | None]]:
        result: dict[str, dict[str, str | bool | None]] = {}
        for name in self.names:
            available, reason = self._backends[name].availability()
            result[name] = {"available": bool(available), "reason_code": reason}
        return result

    def require(self, name: str) -> SpeechTrainingBackend:
        normalized = str(name or "").strip().casefold()
        backend = self._backends.get(normalized)
        if backend is None:
            raise SpeechTrainingBackendError(
                "speech_backend_not_admitted",
                "speech backend is not enabled in this worker",
            )
        available, reason = backend.availability()
        if not available:
            raise SpeechTrainingBackendError(
                str(reason or "speech_backend_unavailable"),
                "speech backend is unavailable",
                retryable=True,
            )
        return backend
