"""Versioned binding between a Hub job attempt and worker-downloaded artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

ARTIFACT_STORAGE_BINDING_SCHEMA = "ananta.lora-training.artifact-storage.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class MlInternTrainingArtifactBinding:
    """Opaque identities from which the Hub derives contained storage paths."""

    tenant_scope_digest: str
    job_id: str
    attempt_id: str

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.tenant_scope_digest):
            raise ValueError("artifact storage tenant scope is invalid")
        for field_name, value in (
            ("job", self.job_id),
            ("attempt", self.attempt_id),
        ):
            if not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"artifact storage {field_name} identity is invalid")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MlInternTrainingArtifactBinding":
        required = {
            "schema",
            "tenant_scope_digest",
            "job_id",
            "attempt_id",
        }
        if set(value) != required:
            raise ValueError("artifact storage binding contract is incomplete")
        if value.get("schema") != ARTIFACT_STORAGE_BINDING_SCHEMA:
            raise ValueError("artifact storage binding schema is invalid")
        return cls(
            tenant_scope_digest=str(value.get("tenant_scope_digest") or ""),
            job_id=str(value.get("job_id") or ""),
            attempt_id=str(value.get("attempt_id") or ""),
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "schema": ARTIFACT_STORAGE_BINDING_SCHEMA,
            "tenant_scope_digest": self.tenant_scope_digest,
            "job_id": self.job_id,
            "attempt_id": self.attempt_id,
        }

    def relative_directory(self, leaf: str) -> str:
        if leaf not in {"adapter", "artifacts"}:
            raise ValueError("artifact storage leaf is invalid")
        return f"tenants/{self.tenant_scope_digest}/jobs/{self.job_id}/attempts/{self.attempt_id}/{leaf}"
