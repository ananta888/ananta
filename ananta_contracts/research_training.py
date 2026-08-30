"""Dependency-free contracts for governed full-model research training."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")

RESEARCH_STAGE_KINDS = frozenset(
    {
        "tokenizer_train",
        "tokenizer_eval",
        "pretrain",
        "base_eval",
        "sft",
        "chat_eval",
        "rl",
        "rl_eval",
        "inference_benchmark",
        "export",
    }
)
RESEARCH_ARTIFACT_KINDS = frozenset(
    {
        "tokenizer",
        "tokenizer_report",
        "base_checkpoint",
        "base_evaluation",
        "sft_checkpoint",
        "chat_evaluation",
        "rl_checkpoint",
        "rl_evaluation",
        "inference_benchmark",
        "model_export",
    }
)
STAGE_CAPABILITIES = {
    "tokenizer_train": "tokenizer_training",
    "tokenizer_eval": "tokenizer_evaluation",
    "pretrain": "full_weight_training",
    "base_eval": "model_evaluation",
    "sft": "full_weight_training",
    "chat_eval": "model_evaluation",
    "rl": "rl_training",
    "rl_eval": "model_evaluation",
    "inference_benchmark": "inference_benchmark",
    "export": "model_export",
}


class ResearchTrainingContractError(ValueError):
    """A closed research-training contract was rejected."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def require_id(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not _ID.fullmatch(text):
        raise ResearchTrainingContractError(f"research_{field}_invalid")
    return text


def require_digest(value: object, field: str) -> str:
    text = str(value or "").strip().lower()
    if not _DIGEST.fullmatch(text):
        raise ResearchTrainingContractError(f"research_{field}_invalid")
    return text


def _exact(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise ResearchTrainingContractError(f"research_{field}_fields_invalid")


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ResearchTrainingContractError(f"research_{field}_invalid")
    return value


def _number(value: object, field: str, minimum: float, maximum: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ResearchTrainingContractError(f"research_{field}_invalid")
    normalized = float(value)
    if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
        raise ResearchTrainingContractError(f"research_{field}_invalid")
    return normalized


def _ids(value: object, field: str, *, maximum: int = 64) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) > maximum:
        raise ResearchTrainingContractError(f"research_{field}_invalid")
    result = tuple(require_id(item, field) for item in value)
    if len(result) != len(set(result)):
        raise ResearchTrainingContractError(f"research_{field}_duplicate")
    return result


def _digests(value: object, field: str, *, maximum: int = 64) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) > maximum:
        raise ResearchTrainingContractError(f"research_{field}_invalid")
    result = tuple(require_digest(item, field) for item in value)
    if len(result) != len(set(result)):
        raise ResearchTrainingContractError(f"research_{field}_duplicate")
    return result


def _evidence_refs(value: object, field: str, prefix: str) -> tuple[str, ...]:
    refs = _ids(value, field, maximum=128)
    if any(not item.startswith(prefix) for item in refs):
        raise ResearchTrainingContractError(f"research_{field}_prefix_invalid")
    return refs


@dataclass(frozen=True, slots=True)
class ResearchBudgetV1:
    gpu_hours: float
    storage_bytes: int
    estimated_cost_microunits: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchBudgetV1:
        _exact(value, {"gpu_hours", "storage_bytes", "estimated_cost_microunits"}, "budget")
        return cls(
            gpu_hours=_number(value.get("gpu_hours"), "budget_gpu_hours", 0, 100_000),
            storage_bytes=_integer(value.get("storage_bytes"), "budget_storage_bytes", 1, 1 << 50),
            estimated_cost_microunits=_integer(
                value.get("estimated_cost_microunits"), "budget_cost", 0, 10**15
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ResearchTrainingRecipeV1:
    SCHEMA: ClassVar[str] = "ananta.research-training-recipe.v1"

    schema: str
    recipe_id: str
    recipe_version: str
    model_family: str
    architecture: str
    depth: int
    context_length: int
    vocab_size: int
    max_steps: int
    seed: int
    precision: str
    world_size: int
    allow_rl: bool
    resolved_hyperparameters: Mapping[str, float | int]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchTrainingRecipeV1:
        _exact(
            value,
            {
                "schema",
                "recipe_id",
                "recipe_version",
                "model_family",
                "architecture",
                "depth",
                "context_length",
                "vocab_size",
                "max_steps",
                "seed",
                "precision",
                "world_size",
                "allow_rl",
                "resolved_hyperparameters",
            },
            "recipe",
        )
        if value.get("schema") != cls.SCHEMA:
            raise ResearchTrainingContractError("research_recipe_schema_invalid")
        precision = str(value.get("precision") or "").strip().lower()
        if precision not in {"float32", "bfloat16", "float16"}:
            raise ResearchTrainingContractError("research_recipe_precision_invalid")
        if not isinstance(value.get("allow_rl"), bool):
            raise ResearchTrainingContractError("research_recipe_allow_rl_invalid")
        raw_hyperparameters = value.get("resolved_hyperparameters")
        if not isinstance(raw_hyperparameters, Mapping) or not 1 <= len(raw_hyperparameters) <= 32:
            raise ResearchTrainingContractError("research_recipe_hyperparameters_invalid")
        hyperparameters: dict[str, float | int] = {}
        for key, item in raw_hyperparameters.items():
            normalized_key = require_id(key, "hyperparameter_name")
            if not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(float(item)):
                raise ResearchTrainingContractError("research_recipe_hyperparameter_value_invalid")
            hyperparameters[normalized_key] = item
        return cls(
            schema=cls.SCHEMA,
            recipe_id=require_id(value.get("recipe_id"), "recipe_id"),
            recipe_version=require_id(value.get("recipe_version"), "recipe_version"),
            model_family=require_id(value.get("model_family"), "model_family"),
            architecture=require_id(value.get("architecture"), "architecture"),
            depth=_integer(value.get("depth"), "recipe_depth", 1, 128),
            context_length=_integer(value.get("context_length"), "recipe_context_length", 128, 262_144),
            vocab_size=_integer(value.get("vocab_size"), "recipe_vocab_size", 256, 1_048_576),
            max_steps=_integer(value.get("max_steps"), "recipe_max_steps", 1, 100_000_000),
            seed=_integer(value.get("seed"), "recipe_seed", 0, (1 << 31) - 1),
            precision=precision,
            world_size=_integer(value.get("world_size"), "recipe_world_size", 1, 1024),
            allow_rl=bool(value.get("allow_rl")),
            resolved_hyperparameters=hyperparameters,
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "resolved_hyperparameters": dict(self.resolved_hyperparameters)}

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class ResearchStageV1:
    stage_id: str
    kind: str
    dependencies: tuple[str, ...]
    required_capability: str
    max_attempts: int
    timeout_seconds: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchStageV1:
        _exact(
            value,
            {"stage_id", "kind", "dependencies", "required_capability", "max_attempts", "timeout_seconds"},
            "stage",
        )
        kind = str(value.get("kind") or "").strip().lower()
        if kind not in RESEARCH_STAGE_KINDS:
            raise ResearchTrainingContractError("research_stage_kind_invalid")
        capability = require_id(value.get("required_capability"), "required_capability")
        if capability != STAGE_CAPABILITIES[kind]:
            raise ResearchTrainingContractError("research_stage_capability_invalid")
        return cls(
            stage_id=require_id(value.get("stage_id"), "stage_id"),
            kind=kind,
            dependencies=_ids(value.get("dependencies"), "stage_dependencies"),
            required_capability=capability,
            max_attempts=_integer(value.get("max_attempts"), "stage_max_attempts", 1, 10),
            timeout_seconds=_integer(value.get("timeout_seconds"), "stage_timeout_seconds", 1, 604_800),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "dependencies": list(self.dependencies)}


@dataclass(frozen=True, slots=True)
class ResearchPipelineV1:
    SCHEMA: ClassVar[str] = "ananta.research-training-pipeline.v1"

    schema: str
    pipeline_id: str
    pipeline_version: str
    stages: tuple[ResearchStageV1, ...]
    automatic_release: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchPipelineV1:
        _exact(value, {"schema", "pipeline_id", "pipeline_version", "stages", "automatic_release"}, "pipeline")
        if value.get("schema") != cls.SCHEMA or not isinstance(value.get("automatic_release"), bool):
            raise ResearchTrainingContractError("research_pipeline_invalid")
        raw_stages = value.get("stages")
        if (
            not isinstance(raw_stages, Sequence)
            or isinstance(raw_stages, (str, bytes))
            or not 1 <= len(raw_stages) <= 32
        ):
            raise ResearchTrainingContractError("research_pipeline_stages_invalid")
        stages = tuple(ResearchStageV1.from_mapping(item) for item in raw_stages if isinstance(item, Mapping))
        if len(stages) != len(raw_stages):
            raise ResearchTrainingContractError("research_pipeline_stages_invalid")
        stage_ids = {item.stage_id for item in stages}
        if len(stage_ids) != len(stages):
            raise ResearchTrainingContractError("research_pipeline_stage_duplicate")
        if any(dependency not in stage_ids for stage in stages for dependency in stage.dependencies):
            raise ResearchTrainingContractError("research_pipeline_dependency_unknown")
        cls._assert_acyclic(stages)
        return cls(
            schema=cls.SCHEMA,
            pipeline_id=require_id(value.get("pipeline_id"), "pipeline_id"),
            pipeline_version=require_id(value.get("pipeline_version"), "pipeline_version"),
            stages=stages,
            automatic_release=bool(value.get("automatic_release")),
        )

    @staticmethod
    def _assert_acyclic(stages: Sequence[ResearchStageV1]) -> None:
        dependencies = {stage.stage_id: set(stage.dependencies) for stage in stages}
        resolved: set[str] = set()
        while len(resolved) < len(stages):
            ready = {
                stage_id
                for stage_id, parents in dependencies.items()
                if stage_id not in resolved and parents <= resolved
            }
            if not ready:
                raise ResearchTrainingContractError("research_pipeline_cycle")
            resolved.update(ready)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "pipeline_id": self.pipeline_id,
            "pipeline_version": self.pipeline_version,
            "stages": [stage.to_dict() for stage in self.stages],
            "automatic_release": self.automatic_release,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class ResearchRunSpecV1:
    SCHEMA: ClassVar[str] = "ananta.research-training-run.v1"

    schema: str
    spec_id: str
    tenant_id: str
    mode: str
    dataset_manifest_digest: str
    source_revision_digest: str
    recipe: ResearchTrainingRecipeV1
    pipeline: ResearchPipelineV1
    budget: ResearchBudgetV1

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchRunSpecV1:
        _exact(
            value,
            {
                "schema",
                "spec_id",
                "tenant_id",
                "mode",
                "dataset_manifest_digest",
                "source_revision_digest",
                "recipe",
                "pipeline",
                "budget",
            },
            "run_spec",
        )
        if value.get("schema") != cls.SCHEMA:
            raise ResearchTrainingContractError("research_run_schema_invalid")
        mode = str(value.get("mode") or "").strip().lower()
        if mode not in {"dry_run", "live"}:
            raise ResearchTrainingContractError("research_run_mode_invalid")
        for field in ("recipe", "pipeline", "budget"):
            if not isinstance(value.get(field), Mapping):
                raise ResearchTrainingContractError(f"research_run_{field}_invalid")
        recipe = ResearchTrainingRecipeV1.from_mapping(value["recipe"])
        pipeline = ResearchPipelineV1.from_mapping(value["pipeline"])
        if any(stage.kind == "rl" for stage in pipeline.stages) and not recipe.allow_rl:
            raise ResearchTrainingContractError("research_run_rl_not_enabled")
        return cls(
            schema=cls.SCHEMA,
            spec_id=require_id(value.get("spec_id"), "spec_id"),
            tenant_id=require_id(value.get("tenant_id"), "tenant_id"),
            mode=mode,
            dataset_manifest_digest=require_digest(value.get("dataset_manifest_digest"), "dataset_manifest_digest"),
            source_revision_digest=require_digest(value.get("source_revision_digest"), "source_revision_digest"),
            recipe=recipe,
            pipeline=pipeline,
            budget=ResearchBudgetV1.from_mapping(value["budget"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "spec_id": self.spec_id,
            "tenant_id": self.tenant_id,
            "mode": self.mode,
            "dataset_manifest_digest": self.dataset_manifest_digest,
            "source_revision_digest": self.source_revision_digest,
            "recipe": self.recipe.to_dict(),
            "pipeline": self.pipeline.to_dict(),
            "budget": self.budget.to_dict(),
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class ResearchArtifactManifestV1:
    SCHEMA: ClassVar[str] = "ananta.research-training-artifact.v1"

    schema: str
    tenant_id: str
    run_id: str
    stage_id: str
    attempt_id: str
    artifact_kind: str
    artifact_digest: str
    size_bytes: int
    parent_artifact_digests: tuple[str, ...]
    recipe_digest: str
    dataset_digest: str
    executable: bool
    source_refs: tuple[str, ...]
    run_refs: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchArtifactManifestV1:
        _exact(
            value,
            {
                "schema",
                "tenant_id",
                "run_id",
                "stage_id",
                "attempt_id",
                "artifact_kind",
                "artifact_digest",
                "size_bytes",
                "parent_artifact_digests",
                "recipe_digest",
                "dataset_digest",
                "executable",
                "source_refs",
                "run_refs",
            },
            "artifact",
        )
        kind = str(value.get("artifact_kind") or "").strip().lower()
        if value.get("schema") != cls.SCHEMA or kind not in RESEARCH_ARTIFACT_KINDS:
            raise ResearchTrainingContractError("research_artifact_invalid")
        if not isinstance(value.get("executable"), bool):
            raise ResearchTrainingContractError("research_artifact_executable_invalid")
        return cls(
            schema=cls.SCHEMA,
            tenant_id=require_id(value.get("tenant_id"), "tenant_id"),
            run_id=require_id(value.get("run_id"), "run_id"),
            stage_id=require_id(value.get("stage_id"), "stage_id"),
            attempt_id=require_id(value.get("attempt_id"), "attempt_id"),
            artifact_kind=kind,
            artifact_digest=require_digest(value.get("artifact_digest"), "artifact_digest"),
            size_bytes=_integer(value.get("size_bytes"), "artifact_size", 1, 1 << 50),
            parent_artifact_digests=_digests(value.get("parent_artifact_digests"), "parent_artifact_digests"),
            recipe_digest=require_digest(value.get("recipe_digest"), "recipe_digest"),
            dataset_digest=require_digest(value.get("dataset_digest"), "dataset_digest"),
            executable=bool(value.get("executable")),
            source_refs=_evidence_refs(value.get("source_refs"), "source_refs", "SRC_"),
            run_refs=_evidence_refs(value.get("run_refs"), "run_refs", "RUN_"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "parent_artifact_digests": list(self.parent_artifact_digests),
            "source_refs": list(self.source_refs),
            "run_refs": list(self.run_refs),
        }


__all__ = [
    "RESEARCH_ARTIFACT_KINDS",
    "RESEARCH_STAGE_KINDS",
    "STAGE_CAPABILITIES",
    "ResearchArtifactManifestV1",
    "ResearchBudgetV1",
    "ResearchPipelineV1",
    "ResearchRunSpecV1",
    "ResearchStageV1",
    "ResearchTrainingContractError",
    "ResearchTrainingRecipeV1",
    "canonical_digest",
    "canonical_json",
    "require_digest",
    "require_id",
]
