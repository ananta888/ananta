"""Deployment-owned Pi switches and profile credentials; never model selection."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator, model_validator

from ananta_contracts.file_credentials import FileCredentialConfigurationError, read_file_managed_token


class NativePiWorkerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool = False
    runtime_root: StrictStr = "/tmp"
    credential_files: dict[StrictStr, StrictStr] = Field(default_factory=dict, max_length=8)

    @field_validator("runtime_root")
    @classmethod
    def _runtime_path(cls, value: str) -> str:
        return cls._absolute_path(value)

    @field_validator("credential_files")
    @classmethod
    def _credential_paths(cls, values: dict[str, str]) -> dict[str, str]:
        for profile, path in values.items():
            if not profile or len(profile) > 256 or any(ord(char) < 33 for char in profile):
                raise ValueError("pi_worker_profile_id_invalid")
            cls._absolute_path(path)
        return values

    @staticmethod
    def _absolute_path(value: str) -> str:
        if (
            not Path(value).is_absolute()
            or len(value) > 4096
            or any(ord(char) < 32 for char in value)
            or ".." in Path(value).parts
        ):
            raise ValueError("pi_worker_managed_path_invalid")
        return value

    @model_validator(mode="after")
    def _enabled_requires_credentials(self) -> NativePiWorkerProfile:
        if self.enabled and not self.credential_files:
            raise ValueError("pi_worker_profile_credentials_required")
        return self


class PiProfileCredentialFiles:
    """Resolve only a preconfigured profile file, without ambient key fallback."""

    def __init__(self, profile: NativePiWorkerProfile) -> None:
        self._files = dict(profile.credential_files)

    def resolve(self, profile_id: str) -> str:
        reference = self._files.get(profile_id)
        if reference is None:
            raise ValueError("pi_profile_credential_not_configured")
        try:
            return read_file_managed_token(reference, description="Pi profile credential", min_bytes=1, max_bytes=8192)
        except FileCredentialConfigurationError as exc:
            raise ValueError("pi_profile_credential_unavailable") from exc
