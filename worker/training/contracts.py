"""Dependency-light wire and execution contracts for local LoRA training."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from ananta_contracts.lora_evaluation import SUPPORTED_SCORERS

CONTRACT_VERSION = "ananta.lora-training.v1"
TRAIN_JOB_TYPE = "train_lora"
EVALUATION_JOB_TYPE = "evaluate_existing_adapter"
SUPPORTED_JOB_TYPE = TRAIN_JOB_TYPE
SUPPORTED_EXPORT_FORMATS = frozenset({"adapter", "merged_16bit", "gguf"})
SUPPORTED_GGUF_QUANTIZATION_METHODS = frozenset({"q4_k_m", "q5_k_m", "q8_0"})
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_RELATIVE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,511}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TrainingContractError(ValueError):
    """A stable, transport-safe contract rejection."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 422,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TrainingContractError("invalid_contract", f"{field} must be an object")
    return value


def _closed_mapping(value: Any, field: str, allowed: frozenset[str]) -> Mapping[str, Any]:
    """Return one closed contract object and reject ambiguous/non-string keys."""

    data = _mapping(value, field)
    if any(not isinstance(key, str) for key in data):
        raise TrainingContractError("invalid_contract_shape", f"{field} contains a non-string field name")
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise TrainingContractError(
            "invalid_contract_shape",
            f"{field} contains unknown fields: {', '.join(unknown[:10])}",
        )
    return data


def _text(value: Any, field: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise TrainingContractError("invalid_contract", f"{field} must be a string")
    result = value.strip()
    if not result or len(result) > maximum:
        raise TrainingContractError("invalid_contract", f"{field} is required and must be at most {maximum} characters")
    return result


def _identifier(value: Any, field: str) -> str:
    result = _text(value, field, maximum=192)
    if not _IDENTIFIER_RE.fullmatch(result):
        raise TrainingContractError("invalid_identifier", f"{field} contains unsupported characters")
    return result


def _relative_ref(value: Any, field: str) -> str:
    result = _text(value, field)
    if not _RELATIVE_REF_RE.fullmatch(result) or result.startswith("/") or ".." in result.split("/") or "//" in result:
        raise TrainingContractError("invalid_path", f"{field} must be a safe relative path")
    return result


def _sha256(value: Any, field: str) -> str:
    result = value.strip() if isinstance(value, str) else ""
    if not _SHA256_RE.fullmatch(result):
        raise TrainingContractError("invalid_hash", f"{field} must be a lowercase SHA-256 digest")
    return result


def _integer(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TrainingContractError("invalid_contract", f"{field} must be an integer")
    result = value
    if result < minimum or result > maximum:
        raise TrainingContractError("invalid_contract", f"{field} must be between {minimum} and {maximum}")
    return result


def _number(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrainingContractError("invalid_contract", f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise TrainingContractError("invalid_contract", f"{field} must be between {minimum} and {maximum}")
    return result


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise TrainingContractError("invalid_contract", f"{field} must be a boolean")
    return value


def _target_modules(value: Any) -> tuple[str, ...]:
    if value is None:
        return ("q_proj", "k_proj", "v_proj", "o_proj")
    if not isinstance(value, list) or not 1 <= len(value) <= 128:
        raise TrainingContractError("invalid_contract", "configuration.target_modules must be a non-empty list")
    modules: list[str] = []
    for item in value:
        module = _identifier(item, "configuration.target_modules[]")
        if module in modules:
            raise TrainingContractError("invalid_contract", "configuration.target_modules must not contain duplicates")
        modules.append(module)
    return tuple(modules)


@dataclass(frozen=True)
class SplitManifest:
    relative_path: str
    sha256: str
    record_count: int

    @classmethod
    def from_mapping(cls, value: Any, field: str) -> "SplitManifest":
        data = _closed_mapping(value, field, frozenset({"relative_path", "sha256", "record_count"}))
        return cls(
            relative_path=_relative_ref(data.get("relative_path"), f"{field}.relative_path"),
            sha256=_sha256(data.get("sha256"), f"{field}.sha256"),
            record_count=_integer(
                data.get("record_count"),
                f"{field}.record_count",
                minimum=1,
                maximum=10_000_000,
            ),
        )


@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    dataset_version: str
    train: SplitManifest
    validation: SplitManifest

    @classmethod
    def from_mapping(cls, value: Any) -> "DatasetManifest":
        data = _closed_mapping(
            value,
            "dataset",
            frozenset({"dataset_id", "dataset_version", "train", "validation"}),
        )
        return cls(
            dataset_id=_identifier(data.get("dataset_id"), "dataset.dataset_id"),
            dataset_version=_identifier(data.get("dataset_version"), "dataset.dataset_version"),
            train=SplitManifest.from_mapping(data.get("train"), "dataset.train"),
            validation=SplitManifest.from_mapping(data.get("validation"), "dataset.validation"),
        )

    @property
    def identity_hash(self) -> str:
        return canonical_sha256(asdict(self))


@dataclass(frozen=True)
class ValidationDatasetManifest:
    dataset_id: str
    dataset_version: str
    validation: SplitManifest

    @classmethod
    def from_mapping(cls, value: Any) -> "ValidationDatasetManifest":
        data = _closed_mapping(
            value,
            "validation_dataset",
            frozenset({"dataset_id", "dataset_version", "validation"}),
        )
        return cls(
            dataset_id=_identifier(data.get("dataset_id"), "validation_dataset.dataset_id"),
            dataset_version=_identifier(data.get("dataset_version"), "validation_dataset.dataset_version"),
            validation=SplitManifest.from_mapping(
                data.get("validation"),
                "validation_dataset.validation",
            ),
        )

    @property
    def identity_hash(self) -> str:
        return canonical_sha256(asdict(self))


@dataclass(frozen=True)
class BaseModelSpec:
    model_id: str
    relative_path: str
    snapshot_hash: str

    @classmethod
    def from_mapping(cls, value: Any) -> "BaseModelSpec":
        data = _closed_mapping(
            value,
            "base_model",
            frozenset({"model_id", "relative_path", "snapshot_hash"}),
        )
        return cls(
            model_id=_text(data.get("model_id"), "base_model.model_id", maximum=256),
            relative_path=_relative_ref(data.get("relative_path"), "base_model.relative_path"),
            snapshot_hash=_sha256(data.get("snapshot_hash"), "base_model.snapshot_hash"),
        )


@dataclass(frozen=True)
class TrainingGovernanceBindings:
    """Opaque Hub-approved spreadsheet provenance carried into Worker artifacts."""

    training_profile_digest: str
    base_model_digest: str
    dataset_manifest_digest: str
    dataset_artifact_digest: str
    dataset_recipe_digest: str
    split_lock_digest: str
    action_schema_digest: str
    serializer_digest: str
    policy_digest: str
    resource_profile_digest: str
    training_admission_digest: str
    governance_digest: str

    @classmethod
    def from_mapping(cls, value: Any) -> "TrainingGovernanceBindings":
        fields = frozenset(
            {
                "training_profile_digest",
                "base_model_digest",
                "dataset_manifest_digest",
                "dataset_artifact_digest",
                "dataset_recipe_digest",
                "split_lock_digest",
                "action_schema_digest",
                "serializer_digest",
                "policy_digest",
                "resource_profile_digest",
                "training_admission_digest",
                "governance_digest",
            }
        )
        data = _closed_mapping(value, "governance", fields)
        bindings = {
            field: _sha256(data.get(field), f"governance.{field}")
            for field in fields
            if field != "governance_digest"
        }
        supplied = _sha256(data.get("governance_digest"), "governance.governance_digest")
        if canonical_sha256(bindings) != supplied:
            raise TrainingContractError(
                "governance_binding_mismatch",
                "governance_digest does not match the supplied bindings",
            )
        return cls(**bindings, governance_digest=supplied)


@dataclass(frozen=True)
class AdapterSpec:
    adapter_id: str
    relative_path: str
    sha256: str

    @classmethod
    def from_mapping(cls, value: Any) -> "AdapterSpec":
        data = _closed_mapping(
            value,
            "adapter",
            frozenset({"adapter_id", "relative_path", "sha256"}),
        )
        return cls(
            adapter_id=_identifier(data.get("adapter_id"), "adapter.adapter_id"),
            relative_path=_relative_ref(data.get("relative_path"), "adapter.relative_path"),
            sha256=_sha256(data.get("sha256"), "adapter.sha256"),
        )


@dataclass(frozen=True)
class TrainingConfiguration:
    seed: int
    max_steps: int
    num_train_epochs: float
    learning_rate: float
    train_batch_size: int
    eval_batch_size: int
    gradient_accumulation_steps: int
    eval_steps: int
    save_steps: int
    early_stopping_patience: int
    lora_rank: int
    lora_alpha: int
    lora_dropout: float
    max_sequence_length: int
    quantization: str
    gradient_checkpointing: bool
    target_modules: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.save_steps % self.eval_steps:
            raise TrainingContractError(
                "invalid_training_schedule",
                "save_steps must be an integer multiple of eval_steps for best-model selection",
            )

    @classmethod
    def from_mapping(cls, value: Any) -> "TrainingConfiguration":
        data = _closed_mapping(
            value,
            "configuration",
            frozenset(
                {
                    "seed",
                    "max_steps",
                    "num_train_epochs",
                    "learning_rate",
                    "train_batch_size",
                    "eval_batch_size",
                    "gradient_accumulation_steps",
                    "eval_steps",
                    "save_steps",
                    "early_stopping_patience",
                    "lora_rank",
                    "lora_alpha",
                    "lora_dropout",
                    "max_sequence_length",
                    "quantization",
                    "gradient_checkpointing",
                    "target_modules",
                }
            ),
        )
        return cls(
            seed=_integer(data.get("seed", 42), "configuration.seed", minimum=0, maximum=2**31 - 1),
            max_steps=_integer(data.get("max_steps", 100), "configuration.max_steps", minimum=1, maximum=10_000_000),
            num_train_epochs=_number(
                data.get("num_train_epochs", 1.0),
                "configuration.num_train_epochs",
                minimum=0.01,
                maximum=10_000.0,
            ),
            learning_rate=_number(
                data.get("learning_rate", 2e-4),
                "configuration.learning_rate",
                minimum=1e-9,
                maximum=1.0,
            ),
            train_batch_size=_integer(
                data.get("train_batch_size", 1),
                "configuration.train_batch_size",
                minimum=1,
                maximum=65_536,
            ),
            eval_batch_size=_integer(
                data.get("eval_batch_size", 1),
                "configuration.eval_batch_size",
                minimum=1,
                maximum=65_536,
            ),
            gradient_accumulation_steps=_integer(
                data.get("gradient_accumulation_steps", 1),
                "configuration.gradient_accumulation_steps",
                minimum=1,
                maximum=1_000_000,
            ),
            eval_steps=_integer(data.get("eval_steps", 10), "configuration.eval_steps", minimum=1, maximum=10_000_000),
            save_steps=_integer(data.get("save_steps", 10), "configuration.save_steps", minimum=1, maximum=10_000_000),
            early_stopping_patience=_integer(
                data.get("early_stopping_patience", 3),
                "configuration.early_stopping_patience",
                minimum=0,
                maximum=10_000,
            ),
            lora_rank=_integer(data.get("lora_rank", 16), "configuration.lora_rank", minimum=1, maximum=4096),
            lora_alpha=_integer(data.get("lora_alpha", 32), "configuration.lora_alpha", minimum=1, maximum=65_536),
            lora_dropout=_number(
                data.get("lora_dropout", 0.05),
                "configuration.lora_dropout",
                minimum=0.0,
                maximum=1.0,
            ),
            max_sequence_length=_integer(
                data.get("max_sequence_length", 2048),
                "configuration.max_sequence_length",
                minimum=32,
                maximum=1_048_576,
            ),
            quantization=_quantization(data.get("quantization", "none")),
            gradient_checkpointing=_boolean(
                data.get("gradient_checkpointing", True),
                "configuration.gradient_checkpointing",
            ),
            target_modules=_target_modules(data.get("target_modules")),
        )

    @property
    def identity_hash(self) -> str:
        return canonical_sha256(asdict(self))


@dataclass(frozen=True)
class AdapterEvaluationConfiguration:
    seed: int
    batch_size: int
    max_sequence_length: int
    max_samples: int
    quantization: str
    scorer_name: str

    @classmethod
    def from_mapping(cls, value: Any) -> "AdapterEvaluationConfiguration":
        data = _closed_mapping(
            value,
            "configuration",
            frozenset(
                {
                    "seed",
                    "batch_size",
                    "max_sequence_length",
                    "max_samples",
                    "quantization",
                    "scorer_name",
                }
            ),
        )
        return cls(
            seed=_integer(data.get("seed", 42), "configuration.seed", minimum=0, maximum=2**31 - 1),
            batch_size=_integer(
                data.get("batch_size", 1),
                "configuration.batch_size",
                minimum=1,
                maximum=65_536,
            ),
            max_sequence_length=_integer(
                data.get("max_sequence_length", 2048),
                "configuration.max_sequence_length",
                minimum=32,
                maximum=1_048_576,
            ),
            max_samples=_integer(
                data.get("max_samples", 100_000),
                "configuration.max_samples",
                minimum=1,
                maximum=10_000_000,
            ),
            quantization=_quantization(data.get("quantization", "none")),
            scorer_name=_scorer_name(data.get("scorer_name", "generic")),
        )

    @property
    def identity_hash(self) -> str:
        return canonical_sha256(asdict(self))


@dataclass(frozen=True)
class CheckpointBinding:
    job_id: str
    source_attempt_id: str
    base_model_hash: str
    dataset_hash: str
    configuration_hash: str
    checkpoint_sha256: str

    @classmethod
    def from_mapping(cls, value: Any) -> "CheckpointBinding":
        data = _closed_mapping(
            value,
            "resume_checkpoint.binding",
            frozenset(
                {
                    "job_id",
                    "source_attempt_id",
                    "base_model_hash",
                    "dataset_hash",
                    "configuration_hash",
                    "checkpoint_sha256",
                }
            ),
        )
        return cls(
            job_id=_identifier(data.get("job_id"), "resume_checkpoint.binding.job_id"),
            source_attempt_id=_identifier(
                data.get("source_attempt_id"),
                "resume_checkpoint.binding.source_attempt_id",
            ),
            base_model_hash=_sha256(data.get("base_model_hash"), "resume_checkpoint.binding.base_model_hash"),
            dataset_hash=_sha256(data.get("dataset_hash"), "resume_checkpoint.binding.dataset_hash"),
            configuration_hash=_sha256(
                data.get("configuration_hash"),
                "resume_checkpoint.binding.configuration_hash",
            ),
            checkpoint_sha256=_sha256(
                data.get("checkpoint_sha256"),
                "resume_checkpoint.binding.checkpoint_sha256",
            ),
        )


@dataclass(frozen=True)
class ResumeCheckpoint:
    relative_path: str
    binding: CheckpointBinding

    @classmethod
    def from_mapping(cls, value: Any) -> "ResumeCheckpoint":
        data = _closed_mapping(
            value,
            "resume_checkpoint",
            frozenset({"relative_path", "binding"}),
        )
        return cls(
            relative_path=_relative_ref(data.get("relative_path"), "resume_checkpoint.relative_path"),
            binding=CheckpointBinding.from_mapping(data.get("binding")),
        )


@dataclass(frozen=True)
class TrainingExportSpec:
    format: str
    quantization_method: str | None = None

    @classmethod
    def from_mapping(cls, value: Any) -> "TrainingExportSpec":
        data = _closed_mapping(
            value,
            "exports item",
            frozenset({"format", "quantization_method"}),
        )
        export_format = _text(data.get("format"), "exports.format", maximum=32).lower()
        if export_format not in SUPPORTED_EXPORT_FORMATS:
            raise TrainingContractError(
                "unsupported_export_format",
                "exports.format must be adapter, merged_16bit, or gguf",
            )
        raw_quantization = data.get("quantization_method")
        quantization = (
            _text(raw_quantization, "exports.quantization_method", maximum=32).lower()
            if raw_quantization is not None
            else None
        )
        if export_format == "gguf":
            if quantization not in SUPPORTED_GGUF_QUANTIZATION_METHODS:
                raise TrainingContractError(
                    "unsupported_quantization",
                    "GGUF exports require q4_k_m, q5_k_m, or q8_0",
                )
        elif quantization is not None:
            raise TrainingContractError(
                "unexpected_quantization",
                "quantization_method is only valid for GGUF exports",
            )
        return cls(format=export_format, quantization_method=quantization)


def _training_exports(value: Any) -> tuple[TrainingExportSpec, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 8:
        raise TrainingContractError(
            "invalid_export_plan",
            "exports must be a non-empty array with at most eight entries",
        )
    exports = tuple(TrainingExportSpec.from_mapping(item) for item in value)
    identities = {(item.format, item.quantization_method) for item in exports}
    if len(identities) != len(exports):
        raise TrainingContractError(
            "duplicate_export",
            "exports must not contain duplicate format and quantization pairs",
        )
    return exports


@dataclass(frozen=True)
class TrainingJobRequest:
    contract_version: str
    job_id: str
    attempt_id: str
    fencing_token: int
    correlation_id: str
    job_type: str
    backend: str
    resource_profile: str
    tenant_scope_digest: str
    workspace_ref: str
    deadline_epoch_ms: int
    base_model: BaseModelSpec
    dataset: DatasetManifest
    configuration: TrainingConfiguration
    governance: TrainingGovernanceBindings | None = None
    exports: tuple[TrainingExportSpec, ...] = ()
    resume_checkpoint: ResumeCheckpoint | None = None
    tenant_storage_key: str | None = None

    def __post_init__(self) -> None:
        if self.governance is not None and self.governance.base_model_digest != self.base_model.snapshot_hash:
            raise TrainingContractError(
                "governance_model_mismatch",
                "governance base_model_digest does not match base_model.snapshot_hash",
            )

    @classmethod
    def from_mapping(cls, value: Any) -> "TrainingJobRequest":
        data = _mapping(value, "request")
        allowed = {
            "contract_version",
            "job_id",
            "attempt_id",
            "fencing_token",
            "correlation_id",
            "job_type",
            "backend",
            "resource_profile",
            "tenant_scope_digest",
            "workspace_ref",
            "deadline_epoch_ms",
            "base_model",
            "dataset",
            "configuration",
            "governance",
            "exports",
            "resume_checkpoint",
            "tenant_storage_key",
        }
        data = _closed_mapping(data, "training request", frozenset(allowed))
        version = _text(data.get("contract_version"), "contract_version", maximum=64)
        if version != CONTRACT_VERSION:
            raise TrainingContractError("unsupported_contract_version", f"unsupported contract_version: {version}")
        job_type = _text(data.get("job_type"), "job_type", maximum=64)
        if job_type != TRAIN_JOB_TYPE:
            raise TrainingContractError("unsupported_job_type", f"unsupported job_type: {job_type}")
        resume_value = data.get("resume_checkpoint")
        return cls(
            contract_version=version,
            job_id=_identifier(data.get("job_id"), "job_id"),
            attempt_id=_identifier(data.get("attempt_id"), "attempt_id"),
            fencing_token=_integer(data.get("fencing_token"), "fencing_token", minimum=1, maximum=2**255 - 1),
            correlation_id=_identifier(data.get("correlation_id"), "correlation_id"),
            job_type=job_type,
            backend=_identifier(data.get("backend"), "backend").lower(),
            resource_profile=_resource_profile(data.get("resource_profile", "mock")),
            tenant_scope_digest=_sha256(data.get("tenant_scope_digest"), "tenant_scope_digest"),
            workspace_ref=_relative_ref(data.get("workspace_ref"), "workspace_ref"),
            deadline_epoch_ms=_integer(
                data.get("deadline_epoch_ms"),
                "deadline_epoch_ms",
                minimum=1,
                maximum=2**63 - 1,
            ),
            base_model=BaseModelSpec.from_mapping(data.get("base_model")),
            dataset=DatasetManifest.from_mapping(data.get("dataset")),
            configuration=TrainingConfiguration.from_mapping(data.get("configuration")),
            governance=(
                TrainingGovernanceBindings.from_mapping(data["governance"])
                if data.get("governance") is not None
                else None
            ),
            exports=_training_exports(data.get("exports")),
            resume_checkpoint=ResumeCheckpoint.from_mapping(resume_value) if resume_value is not None else None,
            tenant_storage_key=_sha256(
                data.get("tenant_storage_key")
                or data.get("tenant_scope_digest"),
                "tenant_storage_key",
            ),
        )

    @property
    def request_hash(self) -> str:
        return canonical_sha256(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not self.exports:
            payload.pop("exports")
        if self.governance is None:
            payload.pop("governance")
        return payload

    def validate_resume_binding(self) -> None:
        if self.resume_checkpoint is None:
            return
        binding = self.resume_checkpoint.binding
        expected = {
            "base_model_hash": self.base_model.snapshot_hash,
            "dataset_hash": self.dataset.identity_hash,
            "configuration_hash": self.configuration.identity_hash,
        }
        actual = {
            "base_model_hash": binding.base_model_hash,
            "dataset_hash": binding.dataset_hash,
            "configuration_hash": binding.configuration_hash,
        }
        if actual != expected:
            raise TrainingContractError(
                "checkpoint_binding_mismatch",
                "resume checkpoint is not bound to this base model, dataset and configuration",
            )


@dataclass(frozen=True)
class AdapterEvaluationJobRequest:
    contract_version: str
    job_id: str
    attempt_id: str
    fencing_token: int
    correlation_id: str
    job_type: str
    backend: str
    resource_profile: str
    tenant_scope_digest: str
    workspace_ref: str
    deadline_epoch_ms: int
    base_model: BaseModelSpec
    adapter: AdapterSpec
    validation_dataset: ValidationDatasetManifest
    configuration: AdapterEvaluationConfiguration
    tenant_storage_key: str | None = None

    @classmethod
    def from_mapping(cls, value: Any) -> "AdapterEvaluationJobRequest":
        data = _mapping(value, "request")
        allowed = {
            "contract_version",
            "job_id",
            "attempt_id",
            "fencing_token",
            "correlation_id",
            "job_type",
            "backend",
            "resource_profile",
            "tenant_scope_digest",
            "workspace_ref",
            "deadline_epoch_ms",
            "base_model",
            "adapter",
            "validation_dataset",
            "configuration",
            "tenant_storage_key",
        }
        data = _closed_mapping(data, "evaluation request", frozenset(allowed))
        version = _text(data.get("contract_version"), "contract_version", maximum=64)
        if version != CONTRACT_VERSION:
            raise TrainingContractError("unsupported_contract_version", f"unsupported contract_version: {version}")
        job_type = _text(data.get("job_type"), "job_type", maximum=64)
        if job_type != EVALUATION_JOB_TYPE:
            raise TrainingContractError("unsupported_job_type", f"unsupported job_type: {job_type}")
        return cls(
            contract_version=version,
            job_id=_identifier(data.get("job_id"), "job_id"),
            attempt_id=_identifier(data.get("attempt_id"), "attempt_id"),
            fencing_token=_integer(data.get("fencing_token"), "fencing_token", minimum=1, maximum=2**255 - 1),
            correlation_id=_identifier(data.get("correlation_id"), "correlation_id"),
            job_type=job_type,
            backend=_identifier(data.get("backend"), "backend").lower(),
            resource_profile=_resource_profile(data.get("resource_profile", "mock")),
            tenant_scope_digest=_sha256(data.get("tenant_scope_digest"), "tenant_scope_digest"),
            workspace_ref=_relative_ref(data.get("workspace_ref"), "workspace_ref"),
            deadline_epoch_ms=_integer(
                data.get("deadline_epoch_ms"),
                "deadline_epoch_ms",
                minimum=1,
                maximum=2**63 - 1,
            ),
            base_model=BaseModelSpec.from_mapping(data.get("base_model")),
            adapter=AdapterSpec.from_mapping(data.get("adapter")),
            validation_dataset=ValidationDatasetManifest.from_mapping(data.get("validation_dataset")),
            configuration=AdapterEvaluationConfiguration.from_mapping(data.get("configuration")),
            tenant_storage_key=_sha256(
                data.get("tenant_storage_key")
                or data.get("tenant_scope_digest"),
                "tenant_storage_key",
            ),
        )

    @property
    def request_hash(self) -> str:
        return canonical_sha256(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


JobRequest = TrainingJobRequest | AdapterEvaluationJobRequest


def parse_job_request(value: Any) -> JobRequest:
    data = _mapping(value, "request")
    job_type = _text(data.get("job_type"), "job_type", maximum=64)
    if job_type == TRAIN_JOB_TYPE:
        return TrainingJobRequest.from_mapping(data)
    if job_type == EVALUATION_JOB_TYPE:
        return AdapterEvaluationJobRequest.from_mapping(data)
    raise TrainingContractError("unsupported_job_type", f"unsupported job_type: {job_type}")


def _resource_profile(value: Any) -> str:
    profile = _identifier(value, "resource_profile").lower()
    if profile not in {"mock", "cpu", "nvidia"}:
        raise TrainingContractError("resource_profile_invalid", "resource_profile must be mock, cpu, or nvidia")
    return profile


def canonical_sha256(value: Any) -> str:
    _validate_canonical_json_value(value)
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TrainingContractError(
            "invalid_contract",
            "canonical contract JSON contains an unsupported or non-finite value",
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _validate_canonical_json_value(value: Any, *, depth: int = 0) -> None:
    if depth > 64:
        raise TrainingContractError("invalid_contract", "canonical contract JSON exceeds its nesting bound")
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TrainingContractError("invalid_contract", "canonical contract JSON contains a non-finite number")
        return
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TrainingContractError("invalid_contract", "canonical contract JSON contains a non-string field name")
        for child in value.values():
            _validate_canonical_json_value(child, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _validate_canonical_json_value(child, depth=depth + 1)
        return
    raise TrainingContractError("invalid_contract", "canonical contract JSON contains a non-JSON value")


def _quantization(value: Any) -> str:
    result = value.strip().lower() if isinstance(value, str) else ""
    if result not in {"none", "4bit", "8bit"}:
        raise TrainingContractError(
            "invalid_contract",
            "configuration.quantization must be none, 4bit, or 8bit",
        )
    return result


def _scorer_name(value: Any) -> str:
    result = value.strip().lower() if isinstance(value, str) else ""
    if result not in SUPPORTED_SCORERS:
        raise TrainingContractError(
            "invalid_contract",
            "configuration.scorer_name is not supported",
        )
    return result
