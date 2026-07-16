from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

CONTRACT_VERSION = "ananta.ml-intern-training.v2"
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
BACKENDS = frozenset({"mock", "peft_trl", "unsloth"})
MODES = frozenset({"dry_run", "live"})
GPU_PROFILES = frozenset({"rtx3080-safe", "generic-safe", "none"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")

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


class MlInternTrainingContractError(ValueError):
    def __init__(self, reason_code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code


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
        method = str(value.get("method") or "qlora").strip().lower()
        if method not in {"lora", "qlora"}:
            raise MlInternTrainingContractError("training_method_invalid", "method must be lora or qlora")
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
        hyperparameters = value.get("hyperparameters") or {}
        if not isinstance(hyperparameters, Mapping):
            raise MlInternTrainingContractError("hyperparameters_invalid", "hyperparameters must be an object")
        cls._validate_hyperparameters(hyperparameters)
        request_spec = {key: child for key, child in value.items()}
        request_spec.pop("base_model_id", None)
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
            if key in values and values[key] is not None and (
                isinstance(values[key], bool) or not isinstance(values[key], int)
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
    return result
