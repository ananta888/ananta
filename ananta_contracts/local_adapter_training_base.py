"""Closed contracts for immutable local-adapter training-base pins."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_CATALOG_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class TrainingBaseArtifactPin(_Closed):
    relative_path: str
    role: Literal[
        "checkpoint",
        "tokenizer",
        "weights",
        "weights_index",
        "configuration",
        "chat_template",
        "license",
        "documentation",
        "repository_metadata",
    ]
    size_bytes: int = Field(gt=0)
    sha256: str

    @field_validator("relative_path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or value in {"", "."}:
            raise ValueError("local_adapter_training_base_path_invalid")
        return str(path)

    @field_validator("sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("local_adapter_training_base_sha256_invalid")
        return value


class TrainingRuntimePin(_Closed):
    package: Literal["cactus-needle"]
    version: str
    distribution_sha256: str
    source_revision: str

    @field_validator("distribution_sha256")
    @classmethod
    def _distribution_sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("local_adapter_training_runtime_sha256_invalid")
        return value

    @field_validator("source_revision")
    @classmethod
    def _source_revision(cls, value: str) -> str:
        if _REVISION.fullmatch(value) is None:
            raise ValueError("local_adapter_training_runtime_revision_invalid")
        return value


class ServingBaselineBinding(_Closed):
    format: Literal["cact", "gguf-q8_0"]
    sha256: str
    compatibility_basis: Literal["same_upstream_revision", "same_agentic_model_release"]

    @field_validator("sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("local_adapter_serving_baseline_sha256_invalid")
        return value


class LocalAdapterTrainingBasePin(_Closed):
    catalog_id: str
    release_target: Literal["needle2", "lfm2.5-2.6b-agentic"]
    training_backend: Literal["needle", "peft_trl"]
    upstream_model_id: str
    upstream_revision: str
    license_id: Literal["Apache-2.0", "LFM-1.0"]
    source_url: str
    snapshot_tree_sha256: str
    artifacts: tuple[TrainingBaseArtifactPin, ...]
    serving_baseline: ServingBaselineBinding
    training_runtime: TrainingRuntimePin | None = None

    @field_validator("catalog_id")
    @classmethod
    def _catalog_id(cls, value: str) -> str:
        normalized = value.lower()
        if _CATALOG_ID.fullmatch(normalized) is None:
            raise ValueError("local_adapter_training_base_catalog_id_invalid")
        return normalized

    @field_validator("upstream_revision")
    @classmethod
    def _revision(cls, value: str) -> str:
        if _REVISION.fullmatch(value) is None:
            raise ValueError("local_adapter_training_base_revision_invalid")
        return value

    @field_validator("snapshot_tree_sha256")
    @classmethod
    def _tree_sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("local_adapter_training_base_tree_sha256_invalid")
        return value

    @model_validator(mode="after")
    def _target_contract(self) -> "LocalAdapterTrainingBasePin":
        paths = [artifact.relative_path for artifact in self.artifacts]
        roles = {artifact.role for artifact in self.artifacts}
        if not paths or len(paths) != len(set(paths)):
            raise ValueError("local_adapter_training_base_artifacts_invalid")
        if self.snapshot_tree_sha256 != self.computed_tree_sha256:
            raise ValueError("local_adapter_training_base_tree_sha256_mismatch")
        if self.release_target == "needle2":
            if (
                self.training_backend != "needle"
                or self.upstream_model_id != "Cactus-Compute/needle2"
                or self.license_id != "Apache-2.0"
                or self.training_runtime is None
                or self.serving_baseline.format != "cact"
                or not {"checkpoint", "tokenizer"}.issubset(roles)
            ):
                raise ValueError("local_adapter_needle_training_base_invalid")
        elif (
            self.training_backend != "peft_trl"
            or self.upstream_model_id != "LiquidAI/LFM2.5-2.6B"
            or self.upstream_model_id.endswith("-Base")
            or self.license_id != "LFM-1.0"
            or self.training_runtime is not None
            or self.serving_baseline.format != "gguf-q8_0"
            or not {"weights", "weights_index", "tokenizer", "chat_template"}.issubset(roles)
        ):
            raise ValueError("local_adapter_lfm_training_base_invalid")
        return self

    @property
    def computed_tree_sha256(self) -> str:
        canonical = "".join(
            f"{artifact.relative_path}\0{artifact.size_bytes}\0{artifact.sha256}\n"
            for artifact in sorted(self.artifacts, key=lambda item: item.relative_path)
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LocalAdapterTrainingBaseCatalog(_Closed):
    schema_version: Literal["ananta.local-adapter-training-bases.v1"]
    bases: tuple[LocalAdapterTrainingBasePin, ...]

    @model_validator(mode="after")
    def _complete_targets(self) -> "LocalAdapterTrainingBaseCatalog":
        targets = [base.release_target for base in self.bases]
        identifiers = [base.catalog_id for base in self.bases]
        if set(targets) != {"needle2", "lfm2.5-2.6b-agentic"} or len(set(identifiers)) != len(identifiers):
            raise ValueError("local_adapter_training_base_catalog_incomplete")
        return self


__all__ = [
    "LocalAdapterTrainingBaseCatalog",
    "LocalAdapterTrainingBasePin",
    "ServingBaselineBinding",
    "TrainingBaseArtifactPin",
    "TrainingRuntimePin",
]
