"""Closed contracts for revision-bound third-party model artifacts."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MODEL_SOURCE_MANIFEST_SCHEMA = "ananta.model-source-manifest.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}$")
_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ModelLicenseEvidence(_ClosedModel):
    spdx_id: str = Field(min_length=1, max_length=64)
    status: Literal["unknown", "declared", "approved", "rejected"]
    evidence_kind: Literal["license_file", "model_card_metadata", "external_review"]
    evidence_url: str = Field(min_length=8, max_length=2048)
    evidence_sha256: str
    license_text_present: bool

    @field_validator("evidence_sha256")
    @classmethod
    def _digest(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("model_license_evidence_digest_invalid")
        return value

    @model_validator(mode="after")
    def _approved_requires_text(self) -> "ModelLicenseEvidence":
        if self.status == "approved" and not self.license_text_present:
            raise ValueError("model_license_approval_without_text")
        return self


class ModelSourceArtifact(_ClosedModel):
    artifact_id: str
    variant_id: str
    repository_id: str
    revision: str
    source_url: str = Field(min_length=8, max_length=2048)
    relative_path: str = Field(min_length=1, max_length=512)
    sha256: str
    size_bytes: int = Field(ge=1, le=1024 * 1024**3)
    format: Literal["gguf", "safetensors", "json", "jinja", "markdown"]
    quantization: str = Field(min_length=1, max_length=64)
    publisher: str = Field(min_length=1, max_length=256)
    license: ModelLicenseEvidence
    activation: Literal["default_off", "evaluation_only", "eligible"] = "default_off"
    reason_codes: tuple[str, ...] = ()

    @field_validator("artifact_id", "variant_id")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if _IDENTIFIER.fullmatch(value) is None:
            raise ValueError("model_source_identifier_invalid")
        return value

    @field_validator("repository_id")
    @classmethod
    def _repository(cls, value: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", value) is None:
            raise ValueError("model_source_repository_invalid")
        return value

    @field_validator("revision")
    @classmethod
    def _revision(cls, value: str) -> str:
        if _REVISION.fullmatch(value) is None:
            raise ValueError("model_source_revision_invalid")
        return value

    @field_validator("sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("model_source_digest_invalid")
        return value

    @field_validator("relative_path")
    @classmethod
    def _path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\x00" in value:
            raise ValueError("model_source_path_invalid")
        return value

    @field_validator("reason_codes")
    @classmethod
    def _reason_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(values))
        if len(normalized) > 32 or any(
            re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,127}", value) is None
            for value in normalized
        ):
            raise ValueError("model_source_reason_code_invalid")
        return normalized

    @model_validator(mode="after")
    def _eligible_requires_approved_license(self) -> "ModelSourceArtifact":
        if self.activation == "eligible" and self.license.status != "approved":
            raise ValueError("model_source_license_not_approved")
        return self


class ModelSourceManifest(_ClosedModel):
    schema_version: Literal["ananta.model-source-manifest.v1"] = Field(
        default=MODEL_SOURCE_MANIFEST_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    manifest_id: str
    reviewed_at: str
    artifacts: tuple[ModelSourceArtifact, ...]
    upstream_claims_are_release_evidence: Literal[False] = False

    @field_validator("manifest_id")
    @classmethod
    def _manifest_id(cls, value: str) -> str:
        if _IDENTIFIER.fullmatch(value) is None:
            raise ValueError("model_source_manifest_id_invalid")
        return value

    @model_validator(mode="after")
    def _unique_artifacts(self) -> "ModelSourceManifest":
        ids = [item.artifact_id for item in self.artifacts]
        bindings = [(item.repository_id, item.revision, item.relative_path) for item in self.artifacts]
        if not ids or len(ids) != len(set(ids)) or len(bindings) != len(set(bindings)):
            raise ValueError("model_source_artifacts_duplicate")
        return self


__all__ = [
    "MODEL_SOURCE_MANIFEST_SCHEMA",
    "ModelLicenseEvidence",
    "ModelSourceArtifact",
    "ModelSourceManifest",
]
