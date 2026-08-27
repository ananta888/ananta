"""Dependency-free v3 capability contract for optional training backends."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

TRAINING_BACKEND_CAPABILITY_VERSION = "ananta.training-backend-capability.v3"
TRAINING_BACKEND_RESULT_VERSION = "ananta.training-backend-result.v3"

BACKEND_IDS = frozenset(
    {
        "autotrain",
        "axolotl",
        "llamafactory",
        "mock",
        "needle",
        "peft_trl",
        "torchtune",
        "unsloth",
        "unsloth_audio",
        "unsloth_embedding",
        "unsloth_vision",
    }
)
MODALITIES = frozenset({"audio", "embedding", "text", "vision"})
OBJECTIVES = frozenset({"dpo", "orpo", "sft"})
METHODS = frozenset({"full", "lora", "qlora"})
PRECISIONS = frozenset({"bf16", "fp16", "fp32"})
QUANTIZATIONS = frozenset({"4bit", "8bit", "none"})
DISTRIBUTED_MODES = frozenset({"fsdp", "single_device"})
EXPORT_FORMATS = frozenset({"adapter", "gguf", "merged_16bit"})
MATURITY_LEVELS = frozenset({"experimental", "production"})
MAINTENANCE_STATES = frozenset({"active", "unmaintained"})
AVAILABILITY_REASON_CODES = frozenset(
    {
        "backend_disabled",
        "dependency_unavailable",
        "ok",
        "resource_profile_unavailable",
        "upstream_unmaintained",
        "version_mismatch",
    }
)
RESULT_REASON_CODES = frozenset(
    {
        "artifact_invalid",
        "artifact_save_failed",
        "backend_disabled",
        "cancelled",
        "checkpoint_incompatible",
        "config_invalid",
        "dataset_invalid",
        "dependency_unavailable",
        "evaluation_failed",
        "export_failed",
        "model_load_failed",
        "ok",
        "out_of_memory",
        "training_failed",
        "version_mismatch",
    }
)

_CAPABILITY_FIELDS = frozenset(
    {
        "schema_version",
        "backend_id",
        "backend_version",
        "available",
        "reason_code",
        "maturity",
        "maintenance",
        "license_spdx",
        "modalities",
        "objectives",
        "methods",
        "precisions",
        "quantizations",
        "distributed_modes",
        "exports",
        "resume",
        "evaluation",
        "resource_profiles",
    }
)


class TrainingBackendContractError(ValueError):
    """Closed-contract validation failure safe to project across processes."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class TrainingBackendCapability:
    backend_id: str
    backend_version: str
    available: bool
    reason_code: str
    maturity: str
    maintenance: str
    license_spdx: str
    modalities: tuple[str, ...]
    objectives: tuple[str, ...]
    methods: tuple[str, ...]
    precisions: tuple[str, ...]
    quantizations: tuple[str, ...]
    distributed_modes: tuple[str, ...]
    exports: tuple[str, ...]
    resume: bool
    evaluation: bool
    resource_profiles: tuple[str, ...]
    schema_version: str = TRAINING_BACKEND_CAPABILITY_VERSION

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TrainingBackendCapability":
        data = _closed_mapping(value, _CAPABILITY_FIELDS, "training backend capability")
        if data.get("schema_version") != TRAINING_BACKEND_CAPABILITY_VERSION:
            raise TrainingBackendContractError("contract_version_unsupported", "capability version is unsupported")
        backend_id = _choice(data.get("backend_id"), BACKEND_IDS, "backend_id")
        available = _boolean(data.get("available"), "available")
        reason_code = _choice(data.get("reason_code"), AVAILABILITY_REASON_CODES, "reason_code")
        if available != (reason_code == "ok"):
            raise TrainingBackendContractError("availability_inconsistent", "availability and reason_code disagree")
        return cls(
            backend_id=backend_id,
            backend_version=_text(data.get("backend_version"), "backend_version"),
            available=available,
            reason_code=reason_code,
            maturity=_choice(data.get("maturity"), MATURITY_LEVELS, "maturity"),
            maintenance=_choice(data.get("maintenance"), MAINTENANCE_STATES, "maintenance"),
            license_spdx=_text(data.get("license_spdx"), "license_spdx"),
            modalities=_choices(data.get("modalities"), MODALITIES, "modalities"),
            objectives=_choices(data.get("objectives"), OBJECTIVES, "objectives"),
            methods=_choices(data.get("methods"), METHODS, "methods"),
            precisions=_choices(data.get("precisions"), PRECISIONS, "precisions"),
            quantizations=_choices(data.get("quantizations"), QUANTIZATIONS, "quantizations"),
            distributed_modes=_choices(data.get("distributed_modes"), DISTRIBUTED_MODES, "distributed_modes"),
            exports=_choices(data.get("exports"), EXPORT_FORMATS, "exports"),
            resume=_boolean(data.get("resume"), "resume"),
            evaluation=_boolean(data.get("evaluation"), "evaluation"),
            resource_profiles=_choices(
                data.get("resource_profiles"), frozenset({"cpu", "generic-safe", "rtx3080-safe"}), "resource_profiles"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for field in (
            "modalities",
            "objectives",
            "methods",
            "precisions",
            "quantizations",
            "distributed_modes",
            "exports",
            "resource_profiles",
        ):
            payload[field] = list(payload[field])
        return payload

    def require(self, *, modality: str, objective: str, method: str, quantization: str, export: str) -> None:
        requested = {
            "modality": (modality, self.modalities),
            "objective": (objective, self.objectives),
            "method": (method, self.methods),
            "quantization": (quantization, self.quantizations),
            "export": (export, self.exports),
        }
        for field, (value, allowed) in requested.items():
            if value not in allowed:
                raise TrainingBackendContractError(
                    "capability_not_declared", f"backend {self.backend_id} does not declare {field}={value}"
                )


def _closed_mapping(value: Any, allowed: frozenset[str], field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise TrainingBackendContractError("contract_shape_invalid", f"{field} must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise TrainingBackendContractError(
            "contract_shape_invalid", f"{field} contains unknown fields: {', '.join(unknown[:10])}"
        )
    return value


def _text(value: Any, field: str) -> str:
    result = value.strip() if isinstance(value, str) else ""
    if not result or len(result) > 128 or any(character.isspace() for character in result):
        raise TrainingBackendContractError("contract_value_invalid", f"{field} is invalid")
    return result


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise TrainingBackendContractError("contract_value_invalid", f"{field} must be a boolean")
    return value


def _choice(value: Any, allowed: frozenset[str], field: str) -> str:
    result = _text(value, field)
    if result not in allowed:
        raise TrainingBackendContractError("contract_value_invalid", f"{field} is unsupported")
    return result


def _choices(value: Any, allowed: frozenset[str], field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value or len(value) > 32:
        raise TrainingBackendContractError("contract_value_invalid", f"{field} must be a bounded non-empty list")
    values = tuple(_choice(item, allowed, f"{field}[]") for item in value)
    if len(values) != len(set(values)):
        raise TrainingBackendContractError("contract_value_invalid", f"{field} contains duplicates")
    return tuple(sorted(values))


__all__ = [
    "AVAILABILITY_REASON_CODES",
    "BACKEND_IDS",
    "RESULT_REASON_CODES",
    "TRAINING_BACKEND_CAPABILITY_VERSION",
    "TRAINING_BACKEND_RESULT_VERSION",
    "TrainingBackendCapability",
    "TrainingBackendContractError",
]
