"""Dependency-light Hub/worker contract for approved LoRA text inference."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Mapping

CONTRACT_VERSION = "ananta.lora-inference.v1"
GENERATION_CAPABILITY = "ml_intern_lora_text_generation"
MANAGEMENT_CAPABILITY = "ml_intern_lora_cache_management"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_RELATIVE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,511}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class LoraInferenceContractError(ValueError):
    """Fail-closed contract rejection with a stable reason code."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def canonical_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LoraInferenceContractError("invalid_contract", "contract data must be finite JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def require_identifier(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise LoraInferenceContractError("invalid_identifier", f"{field} is invalid")
    return normalized


def require_relative_ref(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if (
        not _RELATIVE_REF.fullmatch(normalized)
        or normalized.startswith("/")
        or "//" in normalized
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise LoraInferenceContractError("invalid_path", f"{field} must be a safe relative path")
    return normalized


def require_sha256(value: Any, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise LoraInferenceContractError("invalid_hash", f"{field} must be a SHA-256")
    return normalized


@dataclass(frozen=True)
class LoraInferenceRequest:
    prompt: str
    base_model: str
    adapter_id: str
    adapter_version: str
    task_kind: str
    task_id: str
    max_new_tokens: int = 512
    temperature: float = 0.2

    def __post_init__(self) -> None:
        if not str(self.prompt or "").strip() or len(self.prompt) > 1_048_576:
            raise LoraInferenceContractError("prompt_invalid", "prompt is required and bounded")
        require_identifier(self.adapter_id, "adapter_id")
        require_identifier(self.adapter_version, "adapter_version")
        require_identifier(self.task_kind, "task_kind")
        require_identifier(self.task_id, "task_id")
        if not str(self.base_model or "").strip() or len(self.base_model) > 512:
            raise LoraInferenceContractError("base_model_invalid", "base_model is required and bounded")
        if isinstance(self.max_new_tokens, bool) or not 1 <= int(self.max_new_tokens) <= 4096:
            raise LoraInferenceContractError("generation_limits_invalid", "max_new_tokens is outside its bounds")
        if isinstance(self.temperature, bool) or not 0.0 <= float(self.temperature) <= 2.0:
            raise LoraInferenceContractError("generation_limits_invalid", "temperature is outside its bounds")


@dataclass(frozen=True)
class MaterializedAdapter:
    adapter_id: str
    version: str
    relative_path: str
    directory_sha256: str
    files: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["files"] = [dict(item) for item in self.files]
        return payload


@dataclass(frozen=True)
class LoraInferenceResult:
    text: str
    worker_id: str
    capability: str
    adapter_id: str
    adapter_version: str
    reason_code: str


def approval_decision(
    *,
    adapter_id: str,
    adapter_version: str,
    adapter_sha256: str,
    base_model: str,
    task_kind: str,
    approved_at: str,
) -> dict[str, Any]:
    binding = {
        "adapter_id": require_identifier(adapter_id, "adapter_id"),
        "adapter_version": require_identifier(adapter_version, "adapter_version"),
        "adapter_sha256": require_sha256(adapter_sha256, "adapter_sha256"),
        "base_model": str(base_model or "").strip(),
        "task_kind": require_identifier(task_kind, "task_kind"),
        "status": "approved",
        "approved_at": str(approved_at or "").strip(),
    }
    if not binding["base_model"] or not binding["approved_at"]:
        raise LoraInferenceContractError("approval_binding_invalid", "approval binding is incomplete")
    return {
        "policy_version": "mlintern-lora-runtime-v2",
        "decision": "adapter_selected",
        "reason_code": "approved_adapter_worker_dispatch",
        "approved_only": True,
        "binding": binding,
        "binding_sha256": canonical_sha256(binding),
    }


def build_worker_envelope(
    *,
    request: LoraInferenceRequest,
    adapter: MaterializedAdapter,
    base_model: Mapping[str, str],
    approval: Mapping[str, Any],
    deadline_epoch_ms: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    if deadline_epoch_ms <= time.time_ns() // 1_000_000:
        raise LoraInferenceContractError("deadline_expired", "inference deadline already expired")
    model = {
        "model_id": str(base_model.get("model_id") or "").strip(),
        "relative_path": require_relative_ref(base_model.get("relative_path"), "base_model.relative_path"),
        "snapshot_hash": require_sha256(base_model.get("snapshot_hash"), "base_model.snapshot_hash"),
    }
    if model["model_id"] != request.base_model:
        raise LoraInferenceContractError("base_model_mismatch", "model catalog entry does not match request")
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": require_identifier(request_id or f"lora-{uuid.uuid4().hex}", "request_id"),
        "task_id": request.task_id,
        "deadline_epoch_ms": int(deadline_epoch_ms),
        "capability": GENERATION_CAPABILITY,
        "operation": "generate",
        "task_kind": request.task_kind,
        "base_model": model,
        "adapter": adapter.to_dict(),
        "approval": dict(approval),
        "prompt": request.prompt,
        "generation": {
            "max_new_tokens": int(request.max_new_tokens),
            "temperature": float(request.temperature),
        },
    }


__all__ = [
    "CONTRACT_VERSION",
    "GENERATION_CAPABILITY",
    "MANAGEMENT_CAPABILITY",
    "LoraInferenceContractError",
    "LoraInferenceRequest",
    "LoraInferenceResult",
    "MaterializedAdapter",
    "approval_decision",
    "build_worker_envelope",
    "canonical_sha256",
    "require_identifier",
    "require_relative_ref",
    "require_sha256",
]
