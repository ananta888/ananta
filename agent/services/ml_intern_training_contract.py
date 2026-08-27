from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from agent.services.local_adapter_release_target import (
    normalize_local_adapter_release_target,
)
from agent.services.ml_intern_provenance_contract import (
    MlInternTrainingContractError,
    normalize_run_ids,
    normalize_source_ids,
)
from ananta_contracts.unsloth_capability import (
    UNSLOTH_FACET_REASON_CODES,
    validate_progress_telemetry,
)

CONTRACT_VERSION = "ananta.ml-intern-training.v2"
UNSLOTH_CAPABILITY_SCHEMA_VERSION = "ananta.unsloth-capabilities.v1"
JOB_STATUSES = frozenset(
    {
        "queued",
        "claimed",
        "running",
        "cancel_requested",
        "cancelled",
        "completed",
        "failed",
        "interrupted",
    }
)
TERMINAL_JOB_STATUSES = frozenset({"cancelled", "completed", "failed"})
JOB_TYPES = frozenset(
    {
        "dataset_validate",
        "train_lora",
        "evaluate_lora",
        "register_adapter",
        "export_adapter",
        "merge_adapter_optional",
    }
)
UNSLOTH_BACKENDS = frozenset(
    {
        "unsloth",
        "unsloth_vision",
        "unsloth_audio",
        "unsloth_embedding",
    }
)
BACKENDS = frozenset(
    {
        "autotrain",
        "axolotl",
        "llamafactory",
        "mock",
        "needle",
        "peft_trl",
        "torchtune",
        *UNSLOTH_BACKENDS,
    }
)
MODES = frozenset({"dry_run", "live"})
GPU_PROFILES = frozenset({"rtx3080-safe", "generic-safe", "none"})
UNSLOTH_EXPORT_FORMATS = frozenset({"adapter", "merged_16bit", "gguf"})
UNSLOTH_GGUF_QUANTIZATION_METHODS = frozenset({"q4_k_m", "q5_k_m", "q8_0"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"claimed", "running", "cancel_requested", "failed"}),
    "claimed": frozenset({"running", "cancel_requested", "interrupted", "failed"}),
    "running": frozenset({"cancel_requested", "cancelled", "completed", "failed", "interrupted"}),
    "cancel_requested": frozenset({"cancelled", "failed", "interrupted"}),
    "interrupted": frozenset({"queued", "claimed", "cancelled", "failed"}),
    "cancelled": frozenset(),
    "completed": frozenset(),
    "failed": frozenset(),
}


@dataclass(frozen=True)
class UnslothCapabilityFacet:
    """One small, independently sourced Unsloth capability."""

    facet_id: str
    available: bool
    reason_code: str | None
    source: str
    operations: tuple[str, ...] = ()
    model_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _CAPABILITY_ID_RE.fullmatch(self.facet_id):
            raise ValueError("unsloth capability facet_id is invalid")
        if not self.available and not str(self.reason_code or "").strip():
            raise ValueError("unavailable Unsloth capability requires a reason_code")
        if self.available and self.reason_code is not None:
            raise ValueError("available Unsloth capability must not carry a reason_code")
        if self.reason_code is not None and self.reason_code not in UNSLOTH_FACET_REASON_CODES:
            raise ValueError("unsloth capability reason_code is invalid")
        if self.source not in {"worker_probe", "hub_policy", "configuration"}:
            raise ValueError("unsloth capability source is invalid")
        for value in (*self.operations, *self.model_kinds):
            if not _CAPABILITY_ID_RE.fullmatch(value):
                raise ValueError("unsloth capability detail is invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.facet_id,
            "available": self.available,
            "reason_code": self.reason_code,
            "source": self.source,
            "operations": list(self.operations),
            "model_kinds": list(self.model_kinds),
        }


@dataclass(frozen=True)
class UnslothCapabilitySnapshot:
    """Composed read model; probing remains behind injected worker/provider ports."""

    operating_mode: str
    facets: tuple[UnslothCapabilityFacet, ...]
    detected_variant: str | None = None
    detected_version: str | None = None

    def __post_init__(self) -> None:
        if self.operating_mode not in {"core_worker", "studio_managed", "external_api"}:
            raise ValueError("unsloth operating mode is invalid")
        facet_ids = [facet.facet_id for facet in self.facets]
        if len(facet_ids) != len(set(facet_ids)):
            raise ValueError("unsloth capability facet IDs must be unique")
        for value in (self.detected_variant, self.detected_version):
            if value is not None and not 1 <= len(value) <= 128:
                raise ValueError("unsloth detected metadata exceeds its bounds")

    def to_mapping(self) -> dict[str, Any]:
        facets = [facet.to_mapping() for facet in sorted(self.facets, key=lambda item: item.facet_id)]
        canonical = {
            "schema_version": UNSLOTH_CAPABILITY_SCHEMA_VERSION,
            "operating_mode": self.operating_mode,
            "detected_variant": self.detected_variant,
            "detected_version": self.detected_version,
            "facets": facets,
        }
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return {
            **canonical,
            "snapshot_id": hashlib.sha256(encoded).hexdigest(),
        }


def normalize_unsloth_exports(value: Any) -> tuple[dict[str, str], ...]:
    """Normalize the bounded post-training export plan without accepting paths."""

    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 8:
        raise MlInternTrainingContractError(
            "unsloth_exports_invalid",
            "exports must be a non-empty array with at most eight entries",
        )
    normalized: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, Mapping) or any(not isinstance(key, str) for key in item):
            raise MlInternTrainingContractError(
                "unsloth_exports_invalid",
                "each export must be an object",
            )
        unknown = sorted(set(item) - {"format", "quantization_method"})
        if unknown:
            raise MlInternTrainingContractError(
                "unsloth_exports_invalid",
                f"export contains unknown fields: {', '.join(unknown[:10])}",
            )
        export_format = str(item.get("format") or "").strip().lower()
        if export_format not in UNSLOTH_EXPORT_FORMATS:
            raise MlInternTrainingContractError(
                "unsloth_export_format_invalid",
                "export format must be adapter, merged_16bit, or gguf",
            )
        quantization = str(item.get("quantization_method") or "").strip().lower()
        if export_format == "gguf":
            if quantization not in UNSLOTH_GGUF_QUANTIZATION_METHODS:
                raise MlInternTrainingContractError(
                    "unsloth_export_quantization_invalid",
                    "GGUF quantization_method must be q4_k_m, q5_k_m, or q8_0",
                )
        elif quantization:
            raise MlInternTrainingContractError(
                "unsloth_export_quantization_invalid",
                "quantization_method is only valid for GGUF exports",
            )
        identity = (export_format, quantization)
        if identity in identities:
            raise MlInternTrainingContractError(
                "unsloth_export_duplicate",
                "exports must not contain duplicate format and quantization pairs",
            )
        identities.add(identity)
        export = {"format": export_format}
        if quantization:
            export["quantization_method"] = quantization
        normalized.append(export)
    return tuple(normalized)


def require_identifier(name: str, value: Any) -> str:
    normalized = str(value or "").strip()
    if not _ID_RE.fullmatch(normalized):
        raise MlInternTrainingContractError(f"{name}_invalid", f"{name} is invalid")
    return normalized


def request_digest(payload: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise MlInternTrainingContractError("job_payload_invalid", "job payload is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def idempotency_digest(*, tenant_id: str, subject: str, key: str) -> str:
    normalized = str(key or "").strip()
    if not 8 <= len(normalized) <= 256 or any(character.isspace() for character in normalized):
        raise MlInternTrainingContractError(
            "idempotency_key_invalid",
            "Idempotency-Key must contain 8..256 non-whitespace characters",
            status_code=400,
        )
    return hashlib.sha256(f"lora-job-v2\0{tenant_id}\0{subject}\0{normalized}".encode()).hexdigest()


def assert_job_transition(current: str, target: str) -> None:
    if current not in JOB_STATUSES or target not in JOB_STATUSES:
        raise MlInternTrainingContractError("job_status_invalid", "job status is invalid")
    if target == current:
        return
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise MlInternTrainingContractError(
            "job_status_transition_invalid",
            f"invalid training job transition {current!r} -> {target!r}",
            status_code=409,
        )


def _local_release_configuration(
    value: Mapping[str, Any],
    *,
    job_type: str,
    backend: str,
) -> tuple[str | None, str]:
    try:
        release_target = normalize_local_adapter_release_target(
            value.get("release_target"),
            job_type=job_type,
            backend=backend,
        )
    except ValueError as exc:
        raise MlInternTrainingContractError(
            str(exc),
            "release_target is incompatible with the requested job or backend",
        ) from exc
    if backend == "needle" and release_target != "needle2":
        raise MlInternTrainingContractError(
            "needle_release_target_required",
            "Needle training requires immutable release_target=needle2 lineage",
        )
    method = str(value.get("method") or "qlora").strip().lower()
    if method not in {"lora", "qlora"}:
        raise MlInternTrainingContractError(
            "training_method_invalid",
            "method must be lora or qlora",
        )
    if backend == "needle" and method != "lora":
        raise MlInternTrainingContractError(
            "needle_training_method_invalid",
            "Needle training requires LoRA without quantized PEFT loading",
        )
    return release_target, method


@dataclass(frozen=True)
class CreateTrainingJobCommand:
    dataset_id: str
    job_type: str
    mode: str
    backend: str
    base_model: str | None
    request_spec: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CreateTrainingJobCommand":
        if not isinstance(value, Mapping):
            raise MlInternTrainingContractError("job_payload_invalid", "JSON object body is required", status_code=400)
        allowed = {
            "dataset_id",
            "job_type",
            "mode",
            "backend",
            "base_model",
            "base_model_id",
            "method",
            "gpu_profile",
            "output_name",
            "hyperparameters",
            "require_dataset_validation",
            "require_secret_scan",
            "approval_id",
            "adapter_id",
            "eval_dataset_id",
            "allow_merge",
            "override_reason",
            "scorer_name",
            "risk_reason",
            "live_confirmed",
            "source_ids",
            "run_ids",
            "exports",
            "release_target",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise MlInternTrainingContractError(
                "job_unknown_fields",
                f"unknown job fields: {', '.join(unknown[:10])}",
            )
        dataset_id = require_identifier("dataset_id", value.get("dataset_id"))
        job_type = str(value.get("job_type") or "train_lora").strip().lower()
        mode = str(value.get("mode") or "dry_run").strip().lower()
        backend = str(value.get("backend") or "mock").strip().lower()
        if job_type not in JOB_TYPES:
            raise MlInternTrainingContractError("job_type_invalid", "job_type is not supported")
        if mode not in MODES:
            raise MlInternTrainingContractError("job_mode_invalid", "mode must be dry_run or live")
        if backend not in BACKENDS:
            raise MlInternTrainingContractError("job_backend_invalid", "backend is not supported")
        release_target, method = _local_release_configuration(
            value,
            job_type=job_type,
            backend=backend,
        )
        gpu_profile = str(value.get("gpu_profile") or "").strip().lower()
        if gpu_profile and gpu_profile not in GPU_PROFILES:
            raise MlInternTrainingContractError("gpu_profile_invalid", "gpu_profile is not supported")
        supplied_base_model = str(value.get("base_model") or "").strip()
        supplied_base_model_id = str(value.get("base_model_id") or "").strip()
        if supplied_base_model and supplied_base_model_id and supplied_base_model != supplied_base_model_id:
            raise MlInternTrainingContractError("base_model_conflict", "base_model aliases disagree")
        base_model = supplied_base_model or supplied_base_model_id or None
        if job_type in {"train_lora", "evaluate_lora", "merge_adapter_optional"} and not base_model:
            raise MlInternTrainingContractError("base_model_required", "base_model is required")
        if job_type == "merge_adapter_optional" and value.get("allow_merge") is not True:
            raise MlInternTrainingContractError("merge_confirmation_required", "allow_merge=true is required")
        if "allow_merge" in value and not isinstance(value.get("allow_merge"), bool):
            raise MlInternTrainingContractError(
                "merge_confirmation_invalid",
                "allow_merge must be a JSON boolean",
            )
        exports = normalize_unsloth_exports(value.get("exports"))
        if exports and job_type != "train_lora":
            raise MlInternTrainingContractError(
                "unsloth_export_job_type_invalid",
                "post-training exports are only valid for train_lora jobs",
            )
        if exports and backend != "unsloth":
            raise MlInternTrainingContractError(
                "unsloth_export_backend_required",
                "post-training exports require the text Unsloth backend",
            )
        if any(item["format"] != "adapter" for item in exports) and value.get("allow_merge") is not True:
            raise MlInternTrainingContractError(
                "merge_confirmation_required",
                "allow_merge=true is required for merged_16bit and GGUF exports",
            )
        hyperparameters = value.get("hyperparameters") or {}
        if not isinstance(hyperparameters, Mapping):
            raise MlInternTrainingContractError("hyperparameters_invalid", "hyperparameters must be an object")
        cls._validate_hyperparameters(hyperparameters)
        request_spec = {key: child for key, child in value.items()}
        request_spec.pop("base_model_id", None)
        if release_target is not None:
            request_spec["release_target"] = release_target
        else:
            request_spec.pop("release_target", None)
        if exports:
            request_spec["exports"] = [dict(item) for item in exports]
        else:
            request_spec.pop("exports", None)
        if base_model is not None:
            request_spec["base_model"] = base_model
        if gpu_profile:
            request_spec["gpu_profile"] = gpu_profile
        request_spec["method"] = method
        for policy_flag in ("require_dataset_validation", "require_secret_scan", "live_confirmed"):
            if policy_flag in value and not isinstance(value[policy_flag], bool):
                raise MlInternTrainingContractError(
                    f"{policy_flag}_invalid",
                    f"{policy_flag} must be a JSON boolean",
                )
        if mode == "live":
            if value.get("live_confirmed") is not True:
                raise MlInternTrainingContractError(
                    "live_confirmation_required",
                    "live training requires live_confirmed=true",
                    status_code=403,
                )
            supplied_risk_reason = value.get("risk_reason")
            risk_reason = supplied_risk_reason.strip() if isinstance(supplied_risk_reason, str) else ""
            if not 8 <= len(risk_reason) <= 500:
                raise MlInternTrainingContractError(
                    "live_risk_reason_required",
                    "live training requires a meaningful 8..500 character risk_reason",
                    status_code=403,
                )
            request_spec["risk_reason"] = risk_reason
        canonical_hyperparameters = dict(hyperparameters)
        if "max_sequence_length" in canonical_hyperparameters:
            canonical_hyperparameters["max_seq_length"] = canonical_hyperparameters.pop("max_sequence_length")
        if "quantization" in canonical_hyperparameters:
            quantization = str(canonical_hyperparameters.pop("quantization") or "none").lower()
            if quantization not in {"none", "4bit"}:
                raise MlInternTrainingContractError("quantization_invalid", "quantization must be none or 4bit")
            canonical_hyperparameters["load_in_4bit"] = quantization == "4bit"
        request_spec["hyperparameters"] = canonical_hyperparameters
        for field_name, identifiers in (
            ("source_ids", normalize_source_ids(value.get("source_ids"))),
            ("run_ids", normalize_run_ids(value.get("run_ids"))),
        ):
            if identifiers:
                request_spec[field_name] = list(identifiers)
            else:
                request_spec.pop(field_name, None)
        return cls(
            dataset_id=dataset_id,
            job_type=job_type,
            mode=mode,
            backend=backend,
            base_model=base_model,
            request_spec=request_spec,
        )

    @staticmethod
    def _validate_hyperparameters(values: Mapping[str, Any]) -> None:
        allowed = {
            "lora_rank",
            "lora_alpha",
            "lora_dropout",
            "target_modules",
            "learning_rate",
            "batch_size",
            "gradient_accumulation_steps",
            "max_steps",
            "num_train_epochs",
            "max_seq_length",
            "max_sequence_length",
            "load_in_4bit",
            "quantization",
            "evaluation_steps",
            "early_stopping_patience",
            "seed",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise MlInternTrainingContractError(
                "hyperparameters_unknown_fields",
                f"unknown hyperparameters: {', '.join(unknown[:10])}",
            )
        integer_fields = {
            "lora_rank",
            "lora_alpha",
            "batch_size",
            "gradient_accumulation_steps",
            "max_steps",
            "max_seq_length",
            "max_sequence_length",
            "evaluation_steps",
            "early_stopping_patience",
            "seed",
        }
        for key in integer_fields:
            if (
                key in values
                and values[key] is not None
                and (isinstance(values[key], bool) or not isinstance(values[key], int))
            ):
                raise MlInternTrainingContractError(
                    "hyperparameter_invalid",
                    f"{key} must be an integer",
                )
        bounds: dict[str, tuple[float, float]] = {
            "lora_rank": (1, 256),
            "lora_alpha": (1, 512),
            "lora_dropout": (0, 0.9),
            "learning_rate": (1e-7, 1.0),
            "batch_size": (1, 128),
            "gradient_accumulation_steps": (1, 1024),
            "max_steps": (1, 1_000_000),
            "num_train_epochs": (0.01, 1000),
            "max_seq_length": (128, 32768),
            "max_sequence_length": (128, 32768),
            "evaluation_steps": (1, 1_000_000),
            "early_stopping_patience": (0, 1000),
            "seed": (0, 2**31 - 1),
        }
        for key, (minimum, maximum) in bounds.items():
            if key not in values or values[key] is None:
                continue
            if isinstance(values[key], bool):
                raise MlInternTrainingContractError("hyperparameter_invalid", f"{key} must be numeric")
            try:
                number = float(values[key])
            except (TypeError, ValueError) as exc:
                raise MlInternTrainingContractError("hyperparameter_invalid", f"{key} must be numeric") from exc
            if not math.isfinite(number) or not minimum <= number <= maximum:
                raise MlInternTrainingContractError("hyperparameter_out_of_bounds", f"{key} is outside safe bounds")
        if "target_modules" in values:
            modules = values["target_modules"]
            if not isinstance(modules, list) or not 1 <= len(modules) <= 64:
                raise MlInternTrainingContractError(
                    "target_modules_invalid", "target_modules must contain 1..64 values"
                )
            if any(not _ID_RE.fullmatch(str(item or "")) for item in modules):
                raise MlInternTrainingContractError("target_modules_invalid", "target_modules contains an invalid name")


def sanitize_event_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a small numeric/reason-code event projection, never samples."""

    text_limits = {
        "phase": 64,
        "status": 64,
        "reason_code": 128,
        "adapter_id": 192,
        "cancel_mode": 32,
    }
    result: dict[str, Any] = {}
    for key, maximum in text_limits.items():
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        normalized = "".join(character for character in value.strip() if character.isprintable())[:maximum]
        if normalized:
            result[key] = normalized

    integer_bounds = {
        "current_step": (0, 1_000_000),
        "max_steps": (1, 1_000_000),
        "queue_position": (0, 10_000),
        "vram_allocated_bytes": (0, 2**63 - 1),
        "vram_peak_bytes": (0, 2**63 - 1),
        "vram_used_bytes": (0, 2**63 - 1),
    }
    for key, (minimum, maximum) in integer_bounds.items():
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            continue
        result[key] = value

    numeric_bounds = {
        "progress_percent": (0.0, 100.0),
        "epoch": (0.0, 1_000_000.0),
        "train_loss": (0.0, 1_000_000.0),
        "eval_loss": (0.0, 1_000_000.0),
        "learning_rate": (0.0, 1.0),
        "tokens_per_second": (0.0, 1_000_000_000_000.0),
        "gpu_utilization_percent": (0.0, 100.0),
    }
    for key, (minimum, maximum) in numeric_bounds.items():
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if math.isfinite(number) and minimum <= number <= maximum:
            result[key] = number

    if isinstance(payload.get("retryable"), bool):
        result["retryable"] = payload["retryable"]
    if payload.get("cancel_mode") in {"cooperative", "forced"}:
        result["cancel_mode"] = payload["cancel_mode"]
    telemetry = payload.get("telemetry")
    if isinstance(telemetry, Mapping):
        try:
            result["telemetry"] = validate_progress_telemetry(telemetry)
        except ValueError:
            pass
    return result
