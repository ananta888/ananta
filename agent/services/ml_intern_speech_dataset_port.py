"""Speech-specific immutable dataset manifest publication boundary."""

from __future__ import annotations

from typing import Mapping, Protocol


class MlInternSpeechDatasetPortError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class MlInternSpeechDatasetPort(Protocol):
    def publish_manifest(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        manifest: Mapping[str, object],
    ) -> bool: ...


class UnavailableMlInternSpeechDatasetPort:
    def publish_manifest(self, **_kwargs: object) -> bool:
        raise MlInternSpeechDatasetPortError("speech_dataset_publish_port_unavailable")


__all__ = [
    "MlInternSpeechDatasetPort",
    "MlInternSpeechDatasetPortError",
    "UnavailableMlInternSpeechDatasetPort",
]
