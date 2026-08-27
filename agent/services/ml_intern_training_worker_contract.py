"""Versioned wire-contract constants and errors for the LoRA worker port."""

from __future__ import annotations

import re

WORKER_CONTRACT_VERSION = "ananta.lora-training.v1"
_WORKER_BASE_PATH = "/internal/v1/lora-training"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,511}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TRAINING_BACKENDS = frozenset(
    {
        "autotrain",
        "axolotl",
        "llamafactory",
        "mock",
        "needle",
        "peft_trl",
        "torchtune",
        "unsloth",
        "unsloth_vision",
        "unsloth_audio",
        "unsloth_embedding",
    }
)
_EVENT_MODALITIES = frozenset({"text", "vision", "audio", "embedding"})
_RESOURCE_ADMISSION_PAYLOAD_FIELDS = frozenset(
    {
        "profile",
        "admitted",
        "estimated_peak_bytes",
        "usable_bytes",
        "reserve_bytes",
        "assumptions",
        "estimate_only",
        "reason_code",
    }
)
_WORKER_STATUS_FIELDS = frozenset(
    {
        "contract_version",
        "job_id",
        "attempt_id",
        "fencing_token",
        "correlation_id",
        "job_type",
        "backend",
        "status",
        "created_at",
        "updated_at",
        "heartbeat_at",
        "progress",
        "metrics",
        "artifacts",
        "resume_checkpoint",
        "storage_usage",
        "cancel_mode",
        "error",
    }
)
_WORKER_EVENT_FIELDS = frozenset(
    {
        "contract_version",
        "sequence",
        "timestamp",
        "job_id",
        "attempt_id",
        "fencing_token",
        "correlation_id",
        "type",
        "payload",
    }
)
_WORKER_EVENT_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "accepted": frozenset({"backend"}),
    "status": frozenset({"status", "reason_code", "retryable"}),
    "phase": frozenset({"phase", "step", "modality"}),
    "progress": frozenset(
        {
            "step",
            "max_steps",
            "epoch",
            "loss",
            "eval_loss",
            "learning_rate",
            "tokens_per_second",
            "gpu_utilization_percent",
            "vram_used_bytes",
            "telemetry",
        }
    ),
    "checkpoint": frozenset({"step", "name", "sha256"}),
    "artifact": frozenset({"name", "sha256", "size_bytes", "media_type"}),
    "resource_admission": _RESOURCE_ADMISSION_PAYLOAD_FIELDS,
}


class MlInternTrainingWorkerTransportError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable
