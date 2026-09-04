"""Assignment, runtime and result contracts for one delegated research stage."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

from ananta_contracts.hub_evidence import validate_hub_evidence_assignment
from ananta_contracts.research_training import (
    RESEARCH_ARTIFACT_KINDS,
    ResearchRunSpecV1,
    ResearchStageV1,
    ResearchTrainingContractError,
    canonical_digest,
    require_digest,
    require_id,
)
from ananta_contracts.research_training_data import ResearchDatasetManifestV1

_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,63}$")
_INPUT_RULES: dict[str, tuple[frozenset[str], tuple[frozenset[str], ...]]] = {
    "tokenizer_train": (frozenset(), ()),
    "tokenizer_eval": (frozenset({"tokenizer"}), (frozenset({"tokenizer"}),)),
    "pretrain": (
        frozenset({"tokenizer", "tokenizer_report", "base_checkpoint"}),
        (frozenset({"tokenizer"}),),
    ),
    "base_eval": (
        frozenset({"tokenizer", "tokenizer_report", "base_checkpoint"}),
        (frozenset({"tokenizer"}), frozenset({"base_checkpoint"})),
    ),
    "sft": (
        frozenset({"tokenizer", "tokenizer_report", "base_checkpoint", "base_evaluation", "sft_checkpoint"}),
        (frozenset({"tokenizer"}), frozenset({"base_checkpoint", "sft_checkpoint"})),
    ),
    "chat_eval": (
        frozenset({"tokenizer", "sft_checkpoint"}),
        (frozenset({"tokenizer"}), frozenset({"sft_checkpoint"})),
    ),
    "rl": (
        frozenset({"tokenizer", "sft_checkpoint", "chat_evaluation", "rl_checkpoint"}),
        (frozenset({"tokenizer"}), frozenset({"sft_checkpoint", "rl_checkpoint"})),
    ),
    "rl_eval": (
        frozenset({"tokenizer", "rl_checkpoint"}),
        (frozenset({"tokenizer"}), frozenset({"rl_checkpoint"})),
    ),
    "inference_benchmark": (
        frozenset({"tokenizer", "base_checkpoint", "sft_checkpoint", "rl_checkpoint", "chat_evaluation"}),
        (
            frozenset({"tokenizer"}),
            frozenset({"base_checkpoint", "sft_checkpoint", "rl_checkpoint"}),
        ),
    ),
    "export": (
        frozenset(
            {
                "base_checkpoint",
                "sft_checkpoint",
                "rl_checkpoint",
                "base_evaluation",
                "chat_evaluation",
                "rl_evaluation",
                "inference_benchmark",
            }
        ),
        (frozenset({"base_checkpoint", "sft_checkpoint", "rl_checkpoint"}),),
    ),
}


def _closed(value: Mapping[str, Any], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise ResearchTrainingContractError(f"research_{name}_fields_invalid")


def _relative_ref(value: object, name: str) -> str:
    text = str(value or "").strip()
    parts = text.split("/")
    if (
        not text
        or len(text) > 512
        or text.startswith("/")
        or "\\" in text
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ResearchTrainingContractError(f"research_{name}_invalid")
    return text


def _version(value: object, name: str) -> str:
    text = str(value or "").strip()
    if _VERSION.fullmatch(text) is None:
        raise ResearchTrainingContractError(f"research_{name}_invalid")
    return text


@dataclass(frozen=True, slots=True)
class ResearchRuntimeManifestV1:
    SCHEMA: ClassVar[str] = "ananta.research-training-runtime.v1"

    schema: str
    repository_revision: str
    image_digest: str
    python_version: str
    torch_version: str
    cuda_version: str
    backend_name: str
    backend_version: str
    hardware_profile_digest: str
    deterministic_algorithms: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchRuntimeManifestV1:
        _closed(
            value,
            {
                "schema",
                "repository_revision",
                "image_digest",
                "python_version",
                "torch_version",
                "cuda_version",
                "backend_name",
                "backend_version",
                "hardware_profile_digest",
                "deterministic_algorithms",
            },
            "runtime_manifest",
        )
        revision = str(value.get("repository_revision") or "").strip().lower()
        if len(revision) not in {40, 64} or any(char not in "0123456789abcdef" for char in revision):
            raise ResearchTrainingContractError("research_repository_revision_invalid")
        if value.get("schema") != cls.SCHEMA or not isinstance(value.get("deterministic_algorithms"), bool):
            raise ResearchTrainingContractError("research_runtime_manifest_invalid")
        return cls(
            schema=cls.SCHEMA,
            repository_revision=revision,
            image_digest=require_digest(value.get("image_digest"), "image_digest"),
            python_version=_version(value.get("python_version"), "python_version"),
            torch_version=_version(value.get("torch_version"), "torch_version"),
            cuda_version=_version(value.get("cuda_version"), "cuda_version"),
            backend_name=require_id(value.get("backend_name"), "backend_name"),
            backend_version=require_id(value.get("backend_version"), "backend_version"),
            hardware_profile_digest=require_digest(
                value.get("hardware_profile_digest"), "hardware_profile_digest"
            ),
            deterministic_algorithms=bool(value["deterministic_algorithms"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class ResearchArtifactInputV1:
    artifact_kind: str
    artifact_digest: str
    size_bytes: int
    relative_ref: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchArtifactInputV1:
        _closed(
            value,
            {"artifact_kind", "artifact_digest", "size_bytes", "relative_ref"},
            "artifact_input",
        )
        artifact_kind = require_id(value.get("artifact_kind"), "artifact_kind")
        if artifact_kind not in RESEARCH_ARTIFACT_KINDS:
            raise ResearchTrainingContractError("research_artifact_kind_invalid")
        size = value.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= 1 << 50:
            raise ResearchTrainingContractError("research_artifact_input_size_invalid")
        return cls(
            artifact_kind=artifact_kind,
            artifact_digest=require_digest(value.get("artifact_digest"), "artifact_digest"),
            size_bytes=size,
            relative_ref=_relative_ref(value.get("relative_ref"), "artifact_input_ref"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ResearchStageAssignmentV1:
    SCHEMA: ClassVar[str] = "ananta.research-training-stage-assignment.v1"

    schema: str
    task_id: str
    assignment_id: str
    dispatch_lease_id: str
    attempt_id: str
    worker_id: str
    quota_reservation_id: str
    run_id: str
    run_spec: ResearchRunSpecV1
    stage: ResearchStageV1
    dataset_manifest: ResearchDatasetManifestV1
    runtime: ResearchRuntimeManifestV1
    inputs: tuple[ResearchArtifactInputV1, ...]
    parameters: Mapping[str, Any]
    workspace_subdir: str
    hub_evidence: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchStageAssignmentV1:
        _closed(
            value,
            {
                "schema",
                "task_id",
                "assignment_id",
                "dispatch_lease_id",
                "attempt_id",
                "worker_id",
                "quota_reservation_id",
                "run_id",
                "run_spec",
                "stage",
                "dataset_manifest",
                "runtime",
                "inputs",
                "parameters",
                "workspace_subdir",
                "hub_evidence",
            },
            "stage_assignment",
        )
        if value.get("schema") != cls.SCHEMA:
            raise ResearchTrainingContractError("research_stage_assignment_schema_invalid")
        mappings = ("run_spec", "stage", "dataset_manifest", "runtime", "hub_evidence")
        if any(not isinstance(value.get(field), Mapping) for field in mappings):
            raise ResearchTrainingContractError("research_stage_assignment_mapping_invalid")
        spec = ResearchRunSpecV1.from_mapping(value["run_spec"])
        stage = ResearchStageV1.from_mapping(value["stage"])
        expected = next((item for item in spec.pipeline.stages if item.stage_id == stage.stage_id), None)
        if expected != stage:
            raise ResearchTrainingContractError("research_stage_assignment_stage_binding_invalid")
        dataset = ResearchDatasetManifestV1.from_mapping(value["dataset_manifest"])
        if dataset.tenant_id != spec.tenant_id or dataset.digest != spec.dataset_manifest_digest:
            raise ResearchTrainingContractError("research_stage_assignment_dataset_binding_invalid")
        raw_inputs = value.get("inputs")
        if not isinstance(raw_inputs, Sequence) or isinstance(raw_inputs, (str, bytes)) or len(raw_inputs) > 64:
            raise ResearchTrainingContractError("research_stage_assignment_inputs_invalid")
        if any(not isinstance(item, Mapping) for item in raw_inputs):
            raise ResearchTrainingContractError("research_stage_assignment_inputs_invalid")
        inputs = tuple(ResearchArtifactInputV1.from_mapping(item) for item in raw_inputs)
        if len({item.artifact_digest for item in inputs}) != len(inputs):
            raise ResearchTrainingContractError("research_stage_assignment_input_duplicate")
        input_kinds = {item.artifact_kind for item in inputs}
        allowed_kinds, required_groups = _INPUT_RULES[stage.kind]
        if not input_kinds <= allowed_kinds or any(not input_kinds & group for group in required_groups):
            raise ResearchTrainingContractError("research_stage_assignment_inputs_incompatible")
        raw_parameters = value.get("parameters")
        if not isinstance(raw_parameters, Mapping) or len(raw_parameters) > 64:
            raise ResearchTrainingContractError("research_stage_assignment_parameters_invalid")
        parameters = dict(raw_parameters)
        try:
            canonical_digest(parameters)
        except (TypeError, ValueError) as exc:
            raise ResearchTrainingContractError("research_stage_assignment_parameters_invalid") from exc
        evidence = validate_hub_evidence_assignment(value["hub_evidence"])
        bindings = {
            "task_id": require_id(value.get("task_id"), "task_id"),
            "assignment_id": require_id(value.get("assignment_id"), "assignment_id"),
            "dispatch_lease_id": require_id(value.get("dispatch_lease_id"), "dispatch_lease_id"),
            "attempt_id": require_id(value.get("attempt_id"), "attempt_id"),
            "worker_id": require_id(value.get("worker_id"), "worker_id"),
            "quota_reservation_id": require_id(
                value.get("quota_reservation_id"), "quota_reservation_id"
            ),
            "run_id": require_id(value.get("run_id"), "run_id"),
        }
        if any(evidence[field] != bindings[field] for field in ("task_id", "assignment_id", "dispatch_lease_id")):
            raise ResearchTrainingContractError("research_stage_assignment_evidence_binding_invalid")
        if tuple(evidence["source_ids"]) != dataset.source_ids:
            raise ResearchTrainingContractError("research_stage_assignment_source_binding_invalid")
        runtime = ResearchRuntimeManifestV1.from_mapping(value["runtime"])
        if runtime.repository_revision != spec.source_revision_digest:
            raise ResearchTrainingContractError("research_stage_assignment_revision_binding_invalid")
        return cls(
            schema=cls.SCHEMA,
            task_id=bindings["task_id"],
            assignment_id=bindings["assignment_id"],
            dispatch_lease_id=bindings["dispatch_lease_id"],
            attempt_id=bindings["attempt_id"],
            worker_id=bindings["worker_id"],
            quota_reservation_id=bindings["quota_reservation_id"],
            run_id=bindings["run_id"],
            run_spec=spec,
            stage=stage,
            dataset_manifest=dataset,
            runtime=runtime,
            inputs=inputs,
            parameters=parameters,
            workspace_subdir=_relative_ref(value.get("workspace_subdir"), "workspace_subdir"),
            hub_evidence=evidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "task_id": self.task_id,
            "assignment_id": self.assignment_id,
            "dispatch_lease_id": self.dispatch_lease_id,
            "attempt_id": self.attempt_id,
            "worker_id": self.worker_id,
            "quota_reservation_id": self.quota_reservation_id,
            "run_id": self.run_id,
            "run_spec": self.run_spec.to_dict(),
            "stage": self.stage.to_dict(),
            "dataset_manifest": self.dataset_manifest.to_dict(),
            "runtime": self.runtime.to_dict(),
            "inputs": [item.to_dict() for item in self.inputs],
            "parameters": dict(self.parameters),
            "workspace_subdir": self.workspace_subdir,
            "hub_evidence": dict(self.hub_evidence),
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


__all__ = [
    "ResearchArtifactInputV1",
    "ResearchRuntimeManifestV1",
    "ResearchStageAssignmentV1",
]
