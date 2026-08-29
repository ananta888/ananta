"""Dependency-free contracts for governed dendritic-memory experiments."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Mapping

DENDRITIC_JOB_SCHEMA = "ananta.dendritic-memory-job.v1"
DENDRITIC_PACK_SCHEMA = "ananta.dendritic-memory-pack.v1"
DENDRITIC_WORKER_CONTRACT = "ananta.dendritic-memory-worker.v1"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_TARGET = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,191}$")


class DendriticRunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"

    def assert_transition(self, target: "DendriticRunState") -> None:
        allowed = {
            self.QUEUED: {self.RUNNING, self.CANCEL_REQUESTED, self.CANCELLED, self.FAILED},
            self.RUNNING: {self.CANCEL_REQUESTED, self.CANCELLED, self.COMPLETED, self.FAILED},
            self.CANCEL_REQUESTED: {self.CANCELLED, self.FAILED},
            self.CANCELLED: set(),
            self.COMPLETED: set(),
            self.FAILED: set(),
        }[self]
        if target not in allowed:
            raise ValueError("dendritic_run_transition_invalid")


@dataclass(frozen=True, slots=True)
class DendriticExperimentConfigV1:
    target_layers: tuple[str, ...] | list[str]
    branch_count: int
    hidden_dimension: int
    top_k: int
    routing_enabled: bool
    readout: str
    max_steps: int
    max_memory_bytes: int
    seed: int
    precision: str
    device_profile: str
    deterministic: bool
    schema: str = "ananta.dendritic-memory-config.v1"

    def __post_init__(self) -> None:
        if self.schema != "ananta.dendritic-memory-config.v1":
            raise ValueError("dendritic_config_schema_invalid")
        targets = tuple(self.target_layers)
        if not 1 <= len(targets) <= 16 or len(targets) != len(set(targets)):
            raise ValueError("dendritic_target_layers_invalid")
        if any(not _TARGET.fullmatch(value) or ".." in value for value in targets):
            raise ValueError("dendritic_target_layer_invalid")
        bounds = (
            (self.branch_count, 2, 64),
            (self.hidden_dimension, 8, 4096),
            (self.top_k, 1, self.branch_count),
            (self.max_steps, 1, 100_000),
            (self.max_memory_bytes, 1_048_576, 4_294_967_296),
            (self.seed, 0, 2**31 - 1),
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high
            for value, low, high in bounds
        ):
            raise ValueError("dendritic_config_bound_invalid")
        if self.readout not in {"residual_sum", "gated_residual"}:
            raise ValueError("dendritic_readout_invalid")
        if self.precision not in {"float32", "bfloat16", "float16"}:
            raise ValueError("dendritic_precision_invalid")
        if self.device_profile not in {"cpu-safe", "rtx3080-safe", "generic-safe"}:
            raise ValueError("dendritic_device_profile_invalid")
        if not isinstance(self.routing_enabled, bool) or not isinstance(self.deterministic, bool):
            raise ValueError("dendritic_config_boolean_invalid")
        object.__setattr__(self, "target_layers", targets)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DendriticExperimentConfigV1":
        if set(raw) != set(cls.__dataclass_fields__):
            raise ValueError("dendritic_config_fields_invalid")
        return cls(**dict(raw))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["target_layers"] = list(self.target_layers)
        return value


@dataclass(frozen=True, slots=True)
class DendriticJobSpecV1:
    tenant_id: str
    spec_id: str
    job_type: str
    mode: str
    dataset_manifest_digest: str
    base_model_id: str
    base_model_snapshot_digest: str
    configuration: DendriticExperimentConfigV1 | Mapping[str, Any]
    parent_pack_digests: tuple[str, ...] | list[str] = ()
    schema: str = DENDRITIC_JOB_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != DENDRITIC_JOB_SCHEMA:
            raise ValueError("dendritic_job_schema_invalid")
        require_id(self.tenant_id, "tenant_id")
        require_id(self.spec_id, "spec_id")
        require_id(self.base_model_id, "base_model_id")
        if self.job_type not in {"train_dendritic_memory", "evaluate_dendritic_memory", "compose_dendritic_memory"}:
            raise ValueError("dendritic_job_type_invalid")
        if self.mode not in {"dry_run", "live"}:
            raise ValueError("dendritic_job_mode_invalid")
        require_digest(self.dataset_manifest_digest, "dataset_manifest_digest")
        require_digest(self.base_model_snapshot_digest, "base_model_snapshot_digest")
        configuration = (
            self.configuration
            if isinstance(self.configuration, DendriticExperimentConfigV1)
            else DendriticExperimentConfigV1.from_mapping(self.configuration)
        )
        parents = tuple(self.parent_pack_digests)
        if len(parents) > 16 or len(parents) != len(set(parents)):
            raise ValueError("dendritic_parent_packs_invalid")
        for digest in parents:
            require_digest(digest, "parent_pack_digest")
        if self.job_type == "compose_dendritic_memory" and len(parents) < 2:
            raise ValueError("dendritic_composition_parents_required")
        if self.job_type != "compose_dendritic_memory" and parents:
            raise ValueError("dendritic_parent_packs_forbidden")
        object.__setattr__(self, "configuration", configuration)
        object.__setattr__(self, "parent_pack_digests", parents)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DendriticJobSpecV1":
        allowed = set(cls.__dataclass_fields__)
        if set(raw) - allowed:
            raise ValueError("dendritic_job_unknown_field")
        return cls(**dict(raw))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["configuration"] = self.configuration.to_dict()
        value["parent_pack_digests"] = list(self.parent_pack_digests)
        return value

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class DendriticMemoryPackManifestV1:
    tenant_id: str
    pack_id: str
    base_model_id: str
    base_model_snapshot_digest: str
    architecture_version: str
    target_layers: tuple[str, ...] | list[str]
    parameter_count: int
    trainable_parameter_count: int
    dataset_manifest_digest: str
    split_digests: Mapping[str, str]
    configuration_digest: str
    metrics_digest: str
    files: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]]
    parent_pack_digests: tuple[str, ...] | list[str] = ()
    experimental: bool = True
    production_eligible: bool = False
    claims_verified: bool = False
    executable: bool = True
    schema: str = DENDRITIC_PACK_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != DENDRITIC_PACK_SCHEMA:
            raise ValueError("dendritic_pack_schema_invalid")
        require_id(self.tenant_id, "tenant_id")
        require_id(self.pack_id, "pack_id")
        require_id(self.base_model_id, "base_model_id")
        require_id(self.architecture_version, "architecture_version")
        if self.architecture_version != "branch-projection-v1":
            raise ValueError("dendritic_pack_architecture_unsupported")
        for value, field in (
            (self.base_model_snapshot_digest, "base_model_snapshot_digest"),
            (self.dataset_manifest_digest, "dataset_manifest_digest"),
            (self.configuration_digest, "configuration_digest"),
            (self.metrics_digest, "metrics_digest"),
        ):
            require_digest(value, field)
        if set(self.split_digests) != {"train", "validation", "test"}:
            raise ValueError("dendritic_pack_splits_invalid")
        for digest in self.split_digests.values():
            require_digest(digest, "split_digest")
        targets = tuple(self.target_layers)
        if not targets or any(not _TARGET.fullmatch(value) for value in targets):
            raise ValueError("dendritic_pack_target_invalid")
        if not 1 <= self.trainable_parameter_count <= self.parameter_count <= 100_000_000:
            raise ValueError("dendritic_pack_parameter_count_invalid")
        files = tuple(dict(item) for item in self.files)
        names = [str(item.get("name") or "") for item in files]
        if len(files) not in {1, 2} or len(names) != len(set(names)) or "weights.safetensors" not in names:
            raise ValueError("dendritic_pack_files_invalid")
        for item in files:
            if set(item) != {"name", "sha256", "size_bytes", "media_type"}:
                raise ValueError("dendritic_pack_file_fields_invalid")
            require_digest(item["sha256"], "file_digest")
            if not isinstance(item["size_bytes"], int) or not 1 <= item["size_bytes"] <= 2_147_483_648:
                raise ValueError("dendritic_pack_file_size_invalid")
            expected_media_type = {
                "weights.safetensors": "application/vnd.safetensors",
                "report.json": "application/json",
            }.get(str(item["name"]))
            if item["media_type"] != expected_media_type:
                raise ValueError("dendritic_pack_file_media_type_invalid")
        if (self.experimental, self.production_eligible, self.claims_verified) != (True, False, False):
            raise ValueError("dendritic_pack_safety_labels_invalid")
        parents = tuple(self.parent_pack_digests)
        if len(parents) > 16 or len(parents) != len(set(parents)):
            raise ValueError("dendritic_pack_parents_invalid")
        for digest in parents:
            require_digest(digest, "parent_pack_digest")
        object.__setattr__(self, "target_layers", targets)
        object.__setattr__(self, "files", files)
        object.__setattr__(self, "parent_pack_digests", parents)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DendriticMemoryPackManifestV1":
        allowed = set(cls.__dataclass_fields__)
        if set(raw) - allowed:
            raise ValueError("dendritic_pack_unknown_field")
        return cls(**dict(raw))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["target_layers"] = list(self.target_layers)
        value["files"] = [dict(item) for item in self.files]
        value["parent_pack_digests"] = list(self.parent_pack_digests)
        return value

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def require_id(value: object, field: str) -> str:
    candidate = str(value or "").strip()
    if not _ID.fullmatch(candidate):
        raise ValueError(f"dendritic_{field}_invalid")
    return candidate


def require_digest(value: object, field: str) -> str:
    candidate = str(value or "").strip()
    if not _DIGEST.fullmatch(candidate):
        raise ValueError(f"dendritic_{field}_invalid")
    return candidate


__all__ = [
    "DENDRITIC_JOB_SCHEMA",
    "DENDRITIC_PACK_SCHEMA",
    "DENDRITIC_WORKER_CONTRACT",
    "DendriticExperimentConfigV1",
    "DendriticJobSpecV1",
    "DendriticMemoryPackManifestV1",
    "DendriticRunState",
    "canonical_digest",
    "canonical_json",
    "require_digest",
    "require_id",
]
