"""Deterministic, dependency-light VRAM admission for bounded GPU profiles."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ananta_contracts.unsloth_capability import worker_gpu_profile_limits

_GIB = 1024**3
_MODEL_SUFFIXES = frozenset({".bin", ".gguf", ".safetensors"})


class TrainingConfigurationPort(Protocol):
    quantization: str
    max_sequence_length: int
    train_batch_size: int
    eval_batch_size: int
    gradient_accumulation_steps: int
    lora_rank: int
    lora_alpha: int
    lora_dropout: float
    target_modules: tuple[str, ...]


class VramAdmissionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class VramAdmission:
    profile: str
    admitted: bool
    estimated_peak_bytes: int
    usable_bytes: int | None
    reserve_bytes: int
    assumptions: tuple[str, ...]
    reason_code: str = "vram_admission_admitted"

    def as_event(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "admitted": self.admitted,
            "estimated_peak_bytes": self.estimated_peak_bytes,
            "usable_bytes": self.usable_bytes,
            "reserve_bytes": self.reserve_bytes,
            "assumptions": list(self.assumptions),
            "estimate_only": True,
        }


@dataclass(frozen=True)
class VramAdmissionPolicy:
    profile: str = "unbounded"
    capacity_bytes: int | None = None
    reserve_bytes: int = 0
    max_sequence_length: int | None = None
    max_batch_size: int | None = None
    max_gradient_accumulation_steps: int | None = None
    max_lora_rank: int | None = None
    max_lora_alpha: int | None = None
    max_lora_dropout: float | None = None
    max_target_modules: int | None = None
    max_model_weight_bytes: int | None = None
    required_quantization: str | None = None

    @classmethod
    def from_environment(cls) -> "VramAdmissionPolicy":
        profile = str(os.getenv("ANANTA_LORA_TRAINING_GPU_PROFILE", "")).strip().lower()
        if not profile:
            resource_profile = str(
                os.getenv("ANANTA_LORA_TRAINING_RESOURCE_PROFILE", "")
            ).strip().lower()
            if resource_profile in {"mock", "cpu"}:
                profile = "none"
            elif resource_profile == "nvidia":
                profile = "generic-safe"
        if profile not in {"rtx3080-safe", "generic-safe", "none"}:
            return cls()
        limits = worker_gpu_profile_limits(profile)
        return cls(
            profile=profile,
            capacity_bytes=limits["capacity_bytes"],
            reserve_bytes=limits["reserve_bytes"],
            max_sequence_length=limits["max_sequence_length"],
            max_batch_size=limits["max_train_batch_size"],
            max_gradient_accumulation_steps=limits["max_gradient_accumulation_steps"],
            max_lora_rank=limits["max_lora_rank"],
            max_lora_alpha=limits["max_lora_alpha"],
            max_lora_dropout=limits["max_lora_dropout"],
            max_target_modules=limits["max_target_modules"],
            max_model_weight_bytes=limits["max_model_weight_bytes"],
            required_quantization=limits["required_quantization"],
        )

    def admit(
        self,
        *,
        model_path: Path,
        configuration: TrainingConfigurationPort,
    ) -> VramAdmission:
        self._validate_configuration(configuration)
        weight_bytes = _bounded_model_bytes(
            model_path,
            max_model_weight_bytes=self.max_model_weight_bytes,
        )
        multiplier = {"4bit": 1.45, "8bit": 1.8, "none": 3.0}.get(configuration.quantization, 3.0)
        activation_bytes = max(
            256 * 1024**2,
            configuration.max_sequence_length
            * configuration.train_batch_size
            * max(configuration.lora_rank, 1)
            * 16_384,
        )
        estimate = int(weight_bytes * multiplier + activation_bytes)
        usable = None if self.capacity_bytes is None else self.capacity_bytes - self.reserve_bytes
        if usable is not None and estimate > usable:
            raise VramAdmissionError(
                "vram_admission_rejected",
                "estimated peak VRAM exceeds the configured profile after its safety reserve",
            )
        return VramAdmission(
            profile=self.profile,
            admitted=True,
            estimated_peak_bytes=estimate,
            usable_bytes=usable,
            reserve_bytes=self.reserve_bytes,
            assumptions=(
                "estimate derived from local weight bytes",
                f"quantization multiplier={multiplier}",
                "activation floor=256MiB",
                f"model class bound={self.max_model_weight_bytes or 'unbounded'} bytes",
                f"adapter bounds=rank:{self.max_lora_rank},alpha:{self.max_lora_alpha},"
                f"dropout:{self.max_lora_dropout},targets:{self.max_target_modules}",
                "estimate is not a hard CUDA quota",
            ),
        )

    def _validate_configuration(self, configuration: TrainingConfigurationPort) -> None:
        bounds = (
            ("max_sequence_length", self.max_sequence_length),
            ("train_batch_size", self.max_batch_size),
            ("eval_batch_size", self.max_batch_size),
            ("gradient_accumulation_steps", self.max_gradient_accumulation_steps),
            ("lora_rank", self.max_lora_rank),
            ("lora_alpha", self.max_lora_alpha),
        )
        for field, maximum in bounds:
            if maximum is not None and int(getattr(configuration, field)) > maximum:
                raise VramAdmissionError(
                    "vram_profile_parameter_rejected",
                    f"{field} exceeds the {self.profile} bound of {maximum}",
                )
        if (
            self.max_lora_dropout is not None
            and float(configuration.lora_dropout) > self.max_lora_dropout
        ):
            raise VramAdmissionError(
                "vram_profile_parameter_rejected",
                f"lora_dropout exceeds the {self.profile} bound of {self.max_lora_dropout}",
            )
        if (
            self.max_target_modules is not None
            and len(configuration.target_modules) > self.max_target_modules
        ):
            raise VramAdmissionError(
                "vram_profile_parameter_rejected",
                f"target_modules exceeds the {self.profile} bound of {self.max_target_modules}",
            )
        if self.required_quantization and configuration.quantization != self.required_quantization:
            raise VramAdmissionError(
                "vram_profile_quantization_rejected",
                f"{self.profile} requires {self.required_quantization} quantization",
            )


def _bounded_model_bytes(
    model_path: Path,
    *,
    max_model_weight_bytes: int | None = None,
) -> int:
    try:
        root = model_path.resolve(strict=True)
    except OSError as exc:
        raise VramAdmissionError("model_path_invalid", "model path is unavailable") from exc
    if not root.is_dir():
        raise VramAdmissionError("model_path_invalid", "model path must be a directory")
    total = 0
    files = 0
    try:
        for candidate in root.rglob("*"):
            if candidate.is_symlink() or not candidate.is_file() or candidate.suffix.lower() not in _MODEL_SUFFIXES:
                continue
            resolved = candidate.resolve(strict=True)
            if root not in resolved.parents:
                raise VramAdmissionError("model_path_escape", "model weights escape the admitted root")
            files += 1
            if files > 100_000:
                raise VramAdmissionError("model_file_count_exceeded", "model contains too many weight files")
            total += resolved.stat().st_size
            if max_model_weight_bytes is not None and total > max_model_weight_bytes:
                raise VramAdmissionError(
                    "vram_profile_model_class_rejected",
                    "model weight class exceeds the configured GPU profile",
                )
            if total > 1024 * _GIB:
                raise VramAdmissionError("model_size_exceeded", "model weights exceed the admission bound")
    except VramAdmissionError:
        raise
    except OSError as exc:
        raise VramAdmissionError("model_path_invalid", "model weights could not be inspected") from exc
    if total <= 0:
        raise VramAdmissionError("model_weights_missing", "model contains no admitted local weight files")
    return total
