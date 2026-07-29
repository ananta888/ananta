"""Validation and filesystem helpers for the isolated LoRA worker transport."""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from agent.services.ml_intern_training_worker_contract import (
    _ARTIFACT_NAME,
    _EVENT_MODALITIES,
    _IDENTIFIER,
    _RESOURCE_ADMISSION_PAYLOAD_FIELDS,
    _SHA256,
    _WORKER_EVENT_FIELDS,
    _WORKER_EVENT_PAYLOAD_FIELDS,
    _WORKER_STATUS_FIELDS,
    WORKER_CONTRACT_VERSION,
    MlInternTrainingWorkerTransportError,
)
from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_token,
)
from ananta_contracts.unsloth_capability import (
    UnslothWorkerCapabilityContractError,
    progress_telemetry,
    validate_progress_telemetry,
)


def _worker_token_from_environment() -> str:
    inline = str(os.getenv("ANANTA_LORA_TRAINING_TOKEN", "")).strip()
    path = str(os.getenv("ANANTA_LORA_TRAINING_TOKEN_FILE", "")).strip()
    if path:
        try:
            file_token = read_file_managed_token(
                path,
                description="LoRA training worker token file",
                min_bytes=24,
                max_bytes=16_384,
            )
        except FileCredentialConfigurationError as exc:
            raise RuntimeError(str(exc)) from exc
        if inline and inline != file_token:
            raise RuntimeError("inline and file-managed LoRA training worker tokens conflict")
        return file_token
    if len(inline) < 24:
        raise RuntimeError("LoRA training worker URL is configured without a valid bearer token")
    return inline


def _normalize_model_catalog(value: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for model_id, raw in value.items():
        key = str(model_id or "").strip()
        if not key or len(key) > 256 or not isinstance(raw, Mapping):
            raise ValueError("LoRA training model catalog contains an invalid model")
        relative = _safe_relative(str(raw.get("relative_path") or ""), "base model path")
        digest = str(raw.get("snapshot_hash") or "").strip().lower()
        if not _SHA256.fullmatch(digest):
            raise ValueError("LoRA training model catalog contains an invalid snapshot hash")
        result[key] = {"relative_path": relative, "snapshot_hash": digest}
    if not result:
        raise ValueError("LoRA training model catalog must contain at least one model")
    return result


def _worker_configuration(spec: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(spec.get("hyperparameters") or {})
    max_steps = int(values.get("max_steps") or 100)
    evaluation_steps = int(values.get("evaluation_steps") or min(10, max_steps))
    return {
        "seed": int(values.get("seed") or 42),
        "max_steps": max_steps,
        "num_train_epochs": float(values.get("num_train_epochs") or 1.0),
        "learning_rate": float(values.get("learning_rate") or 2e-4),
        "train_batch_size": int(values.get("batch_size") or 1),
        "eval_batch_size": int(values.get("batch_size") or 1),
        "gradient_accumulation_steps": int(values.get("gradient_accumulation_steps") or 1),
        "eval_steps": evaluation_steps,
        "save_steps": evaluation_steps,
        "early_stopping_patience": int(values.get("early_stopping_patience") or 0),
        "lora_rank": int(values.get("lora_rank") or 16),
        "lora_alpha": int(values.get("lora_alpha") or 32),
        "lora_dropout": float(values.get("lora_dropout") or 0.05),
        "max_sequence_length": int(values.get("max_seq_length") or 2048),
        "quantization": "4bit" if bool(values.get("load_in_4bit", spec.get("method") == "qlora")) else "none",
        "gradient_checkpointing": True,
        "target_modules": list(values.get("target_modules") or ["q_proj", "k_proj", "v_proj", "o_proj"]),
    }


def _worker_exports(spec: Mapping[str, Any], *, backend: str) -> list[dict[str, str]]:
    raw = spec.get("exports")
    if raw is None:
        return []
    if backend != "unsloth":
        raise MlInternTrainingWorkerTransportError(
            "unsloth_export_backend_required",
            "post-training exports require the text Unsloth backend",
            retryable=False,
        )
    if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= 8:
        raise MlInternTrainingWorkerTransportError(
            "unsloth_exports_invalid",
            "exports must be a non-empty array with at most eight entries",
            retryable=False,
        )
    exports: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, Mapping) or any(not isinstance(key, str) for key in item):
            raise MlInternTrainingWorkerTransportError(
                "unsloth_exports_invalid",
                "each export must be an object",
                retryable=False,
            )
        if set(item) - {"format", "quantization_method"}:
            raise MlInternTrainingWorkerTransportError(
                "unsloth_exports_invalid",
                "export contains unknown fields",
                retryable=False,
            )
        export_format = str(item.get("format") or "").strip().lower()
        quantization = str(item.get("quantization_method") or "").strip().lower()
        if export_format not in {"adapter", "merged_16bit", "gguf"}:
            raise MlInternTrainingWorkerTransportError(
                "unsloth_export_format_invalid",
                "export format is not supported",
                retryable=False,
            )
        if export_format == "gguf":
            if quantization not in {"q4_k_m", "q5_k_m", "q8_0"}:
                raise MlInternTrainingWorkerTransportError(
                    "unsloth_export_quantization_invalid",
                    "GGUF quantization_method is not supported",
                    retryable=False,
                )
        elif quantization:
            raise MlInternTrainingWorkerTransportError(
                "unsloth_export_quantization_invalid",
                "quantization_method is only valid for GGUF exports",
                retryable=False,
            )
        identity = (export_format, quantization)
        if identity in identities:
            raise MlInternTrainingWorkerTransportError(
                "unsloth_export_duplicate",
                "exports contain a duplicate format and quantization pair",
                retryable=False,
            )
        identities.add(identity)
        export = {"format": export_format}
        if quantization:
            export["quantization_method"] = quantization
        exports.append(export)
    if any(item["format"] != "adapter" for item in exports) and spec.get("allow_merge") is not True:
        raise MlInternTrainingWorkerTransportError(
            "merge_confirmation_required",
            "allow_merge=true is required for merged_16bit and GGUF exports",
            retryable=False,
        )
    return exports


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _invalid_worker_response(message: str) -> MlInternTrainingWorkerTransportError:
    return MlInternTrainingWorkerTransportError(
        "invalid_worker_response",
        message,
        retryable=False,
    )


def _validate_bounded_json(
    value: Any,
    *,
    field: str,
    depth: int = 0,
    budget: list[int] | None = None,
) -> None:
    """Validate the JSON data model and reject non-finite or resource-heavy trees."""

    remaining = budget if budget is not None else [10_000]
    remaining[0] -= 1
    if remaining[0] < 0 or depth > 12:
        raise _invalid_worker_response(f"{field} exceeds its structural bound")
    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str) and len(value) > 65_536:
            raise _invalid_worker_response(f"{field} contains an oversized string")
        return
    if isinstance(value, int):
        if abs(value) > 2**255 - 1:
            raise _invalid_worker_response(f"{field} contains an out-of-range integer")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _invalid_worker_response(f"{field} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise _invalid_worker_response(f"{field} contains too many fields")
        for key, child in value.items():
            if not isinstance(key, str) or not key or len(key) > 512:
                raise _invalid_worker_response(f"{field} contains an invalid field name")
            _validate_bounded_json(child, field=f"{field}.{key}", depth=depth + 1, budget=remaining)
        return
    if isinstance(value, list):
        if len(value) > 1_000:
            raise _invalid_worker_response(f"{field} contains too many items")
        for index, child in enumerate(value):
            _validate_bounded_json(child, field=f"{field}[{index}]", depth=depth + 1, budget=remaining)
        return
    raise _invalid_worker_response(f"{field} contains a non-JSON value")


def _closed_worker_object(value: Mapping[str, Any], *, field: str, allowed: frozenset[str]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _invalid_worker_response(f"{field} contains unknown fields: {', '.join(unknown[:10])}")


def _validate_worker_error(value: Mapping[str, Any]) -> None:
    _closed_worker_object(
        value,
        field="worker error",
        allowed=frozenset({"code", "message", "retryable"}),
    )
    code = value.get("code")
    message = value.get("message")
    if not isinstance(code, str) or not _IDENTIFIER.fullmatch(code) or len(code) > 128:
        raise _invalid_worker_response("worker error code is invalid")
    if not isinstance(message, str) or not message.strip() or len(message) > 512:
        raise _invalid_worker_response("worker error message is invalid")
    if not isinstance(value.get("retryable"), bool):
        raise _invalid_worker_response("worker error retryable flag is invalid")


def _validate_artifact_metadata(value: Mapping[str, Any]) -> None:
    _closed_worker_object(
        value,
        field="worker artifact metadata",
        allowed=frozenset({"name", "sha256", "size_bytes", "media_type"}),
    )
    _artifact_name(value.get("name"))
    if not isinstance(value.get("sha256"), str) or not _SHA256.fullmatch(str(value["sha256"])):
        raise _invalid_worker_response("worker artifact hash is invalid")
    size = value.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= 2**63 - 1:
        raise _invalid_worker_response("worker artifact size is invalid")
    media_type = value.get("media_type")
    if (
        not isinstance(media_type, str)
        or not 1 <= len(media_type) <= 128
        or "/" not in media_type
        or any(character.isspace() for character in media_type)
    ):
        raise _invalid_worker_response("worker artifact media type is invalid")


def _validate_resume_checkpoint(value: Mapping[str, Any]) -> None:
    _closed_worker_object(
        value,
        field="worker resume checkpoint",
        allowed=frozenset({"relative_path", "binding"}),
    )
    try:
        _safe_relative(str(value.get("relative_path") or ""), "worker checkpoint path")
    except ValueError as exc:
        raise _invalid_worker_response("worker checkpoint path is invalid") from exc
    binding = value.get("binding")
    if not isinstance(binding, Mapping):
        raise _invalid_worker_response("worker checkpoint binding is invalid")
    allowed = frozenset(
        {
            "job_id",
            "source_attempt_id",
            "base_model_hash",
            "dataset_hash",
            "configuration_hash",
            "checkpoint_sha256",
        }
    )
    _closed_worker_object(binding, field="worker checkpoint binding", allowed=allowed)
    for key in ("job_id", "source_attempt_id"):
        if not isinstance(binding.get(key), str) or not _IDENTIFIER.fullmatch(str(binding[key])):
            raise _invalid_worker_response("worker checkpoint identity binding is invalid")
    for key in ("base_model_hash", "dataset_hash", "configuration_hash", "checkpoint_sha256"):
        if not isinstance(binding.get(key), str) or not _SHA256.fullmatch(str(binding[key])):
            raise _invalid_worker_response("worker checkpoint hash binding is invalid")


def _validate_worker_status(value: Mapping[str, Any]) -> None:
    _validate_bounded_json(value, field="worker status")
    _closed_worker_object(value, field="worker status", allowed=_WORKER_STATUS_FIELDS)
    if set(value) != _WORKER_STATUS_FIELDS:
        raise _invalid_worker_response("worker status is missing required result fields")
    if value.get("contract_version") != WORKER_CONTRACT_VERSION:
        raise _invalid_worker_response("worker status contract version is invalid")
    state = value.get("status")
    if state not in {"queued", "running", "cancel_requested", "succeeded", "failed", "cancelled"}:
        raise _invalid_worker_response("worker status value is invalid")
    for key in ("job_id", "attempt_id"):
        if not isinstance(value.get(key), str) or not _IDENTIFIER.fullmatch(str(value[key])):
            raise _invalid_worker_response(f"worker status {key} is invalid")
    fencing_token = value.get("fencing_token")
    if isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or not 1 <= fencing_token <= 2**255 - 1:
        raise _invalid_worker_response("worker status fencing token is invalid")
    if not isinstance(value.get("correlation_id"), str) or not _IDENTIFIER.fullmatch(str(value["correlation_id"])):
        raise _invalid_worker_response("worker status correlation ID is invalid")
    if value.get("job_type") is not None and value.get("job_type") not in {
        "train_lora",
        "evaluate_existing_adapter",
    }:
        raise _invalid_worker_response("worker status job type is invalid")
    if value.get("backend") is not None and (
        not isinstance(value.get("backend"), str) or not _IDENTIFIER.fullmatch(str(value["backend"]))
    ):
        raise _invalid_worker_response("worker status backend is invalid")
    for key in ("created_at", "updated_at", "heartbeat_at"):
        timestamp = value.get(key)
        if timestamp is not None and (
            isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or float(timestamp) < 0
        ):
            raise _invalid_worker_response(f"worker status {key} is invalid")
    progress = value.get("progress")
    if progress is not None:
        if not isinstance(progress, Mapping):
            raise _invalid_worker_response("worker status progress is invalid")
        _closed_worker_object(
            progress,
            field="worker status progress",
            allowed=_WORKER_EVENT_PAYLOAD_FIELDS["progress"],
        )
        if progress:
            _validate_event_payload("progress", progress)
    metrics = value.get("metrics")
    if metrics is not None and not isinstance(metrics, Mapping):
        raise _invalid_worker_response("worker status metrics are invalid")
    artifacts = value.get("artifacts")
    if artifacts is not None:
        if not isinstance(artifacts, list) or len(artifacts) > 64:
            raise _invalid_worker_response("worker status artifacts are invalid")
        for artifact in artifacts:
            if not isinstance(artifact, Mapping):
                raise _invalid_worker_response("worker status artifact metadata is invalid")
            _validate_artifact_metadata(artifact)
    checkpoint = value.get("resume_checkpoint")
    if checkpoint is not None:
        if not isinstance(checkpoint, Mapping):
            raise _invalid_worker_response("worker status resume checkpoint is invalid")
        _validate_resume_checkpoint(checkpoint)
    cancel_mode = value.get("cancel_mode")
    if cancel_mode is not None and cancel_mode not in {"graceful", "forced"}:
        raise _invalid_worker_response("worker status cancel mode is invalid")
    error = value.get("error")
    if error is not None:
        if not isinstance(error, Mapping):
            raise _invalid_worker_response("worker status error is invalid")
        _validate_worker_error(error)
    if state == "failed" and error is None:
        raise _invalid_worker_response("worker failure status is missing its structured error")
    if state == "succeeded" and error is not None:
        raise _invalid_worker_response("worker success status contains an error")
    if state == "cancelled" and cancel_mode not in {"graceful", "forced"}:
        raise _invalid_worker_response("worker cancellation status is missing its cancel mode")


def _validate_worker_event_page(value: Mapping[str, Any]) -> None:
    _validate_bounded_json(value, field="worker event page")
    _closed_worker_object(
        value,
        field="worker event page",
        allowed=frozenset({"contract_version", "job_id", "attempt_id", "events", "next_sequence"}),
    )
    if set(value) != {"contract_version", "job_id", "attempt_id", "events", "next_sequence"}:
        raise _invalid_worker_response("worker event page is missing required fields")
    if value.get("contract_version") != WORKER_CONTRACT_VERSION:
        raise _invalid_worker_response("worker event page contract version is invalid")
    for key in ("job_id", "attempt_id"):
        if not isinstance(value.get(key), str) or not _IDENTIFIER.fullmatch(str(value[key])):
            raise _invalid_worker_response(f"worker event page {key} is invalid")
    events = value.get("events")
    next_sequence = value.get("next_sequence")
    if not isinstance(events, list) or len(events) > 1_000:
        raise _invalid_worker_response("worker event page is invalid")
    if isinstance(next_sequence, bool) or not isinstance(next_sequence, int) or next_sequence < 0:
        raise _invalid_worker_response("worker event cursor is invalid")
    previous = -1
    for event in events:
        if not isinstance(event, Mapping):
            raise _invalid_worker_response("worker event is invalid")
        _validate_worker_event(event)
        sequence = int(event["sequence"])
        if sequence <= previous or sequence > next_sequence:
            raise _invalid_worker_response("worker event sequence is not monotone")
        previous = sequence
    if events and previous != next_sequence:
        raise _invalid_worker_response("worker event cursor does not match its last event")


def _validate_worker_event(value: Mapping[str, Any]) -> None:
    _validate_bounded_json(value, field="worker event")
    _closed_worker_object(value, field="worker event", allowed=_WORKER_EVENT_FIELDS)
    if set(value) != _WORKER_EVENT_FIELDS:
        raise _invalid_worker_response("worker event is missing required fields")
    if value.get("contract_version") != WORKER_CONTRACT_VERSION:
        raise _invalid_worker_response("worker event contract version is invalid")
    for key in ("job_id", "attempt_id", "correlation_id"):
        if not isinstance(value.get(key), str) or not _IDENTIFIER.fullmatch(str(value[key])):
            raise _invalid_worker_response(f"worker event {key} is invalid")
    fencing_token = value.get("fencing_token")
    if isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or not 1 <= fencing_token <= 2**255 - 1:
        raise _invalid_worker_response("worker event fencing token is invalid")
    sequence = value.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise _invalid_worker_response("worker event sequence is invalid")
    timestamp = value.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or float(timestamp) < 0:
        raise _invalid_worker_response("worker event timestamp is invalid")
    event_type = value.get("type")
    allowed_payload = _WORKER_EVENT_PAYLOAD_FIELDS.get(str(event_type))
    if allowed_payload is None:
        raise _invalid_worker_response("worker event type is invalid")
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        raise _invalid_worker_response("worker event payload is invalid")
    _closed_worker_object(payload, field="worker event payload", allowed=allowed_payload)
    _validate_event_payload(str(event_type), payload)


def _validate_event_payload(event_type: str, payload: Mapping[str, Any]) -> None:
    validator = _EVENT_PAYLOAD_VALIDATORS.get(event_type)
    if validator is None:
        raise _invalid_worker_response("worker event type is invalid")
    validator(payload)


def _validate_accepted_event_payload(payload: Mapping[str, Any]) -> None:
    backend = payload.get("backend")
    if not isinstance(backend, str) or not _IDENTIFIER.fullmatch(backend):
        raise _invalid_worker_response("worker accepted event backend is invalid")


def _validate_status_event_payload(payload: Mapping[str, Any]) -> None:
    if payload.get("status") not in {
        "queued",
        "running",
        "cancel_requested",
        "succeeded",
        "failed",
        "cancelled",
    }:
        raise _invalid_worker_response("worker status event state is invalid")
    reason = payload.get("reason_code")
    if not isinstance(reason, str) or len(reason) > 128:
        raise _invalid_worker_response("worker status event reason is invalid")
    if not isinstance(payload.get("retryable"), bool):
        raise _invalid_worker_response("worker status event retryable flag is invalid")


def _validate_phase_event_payload(payload: Mapping[str, Any]) -> None:
    phase = payload.get("phase")
    if not isinstance(phase, str) or not phase or len(phase) > 64:
        raise _invalid_worker_response("worker phase event is invalid")
    step = payload.get("step")
    if step is not None and (isinstance(step, bool) or not isinstance(step, int) or not 0 <= step <= 10_000_000):
        raise _invalid_worker_response("worker phase event step is invalid")
    modality = payload.get("modality")
    if modality is not None and modality not in _EVENT_MODALITIES:
        raise _invalid_worker_response("worker phase event modality is invalid")


def _validate_resource_admission_event_payload(payload: Mapping[str, Any]) -> None:
    required_fields = _RESOURCE_ADMISSION_PAYLOAD_FIELDS.difference({"reason_code"})
    if not required_fields.issubset(payload):
        raise _invalid_worker_response("worker resource admission event is missing required fields")
    profile = payload.get("profile")
    if not isinstance(profile, str) or not profile or len(profile) > 64:
        raise _invalid_worker_response("worker resource admission profile is invalid")
    if not isinstance(payload.get("admitted"), bool) or not isinstance(payload.get("estimate_only"), bool):
        raise _invalid_worker_response("worker resource admission flags are invalid")
    for field_name in ("estimated_peak_bytes", "reserve_bytes"):
        value = payload.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
            raise _invalid_worker_response(f"worker resource admission {field_name} is invalid")
    usable_bytes = payload.get("usable_bytes")
    if usable_bytes is not None and (
        isinstance(usable_bytes, bool) or not isinstance(usable_bytes, int) or not 0 <= usable_bytes <= 2**63 - 1
    ):
        raise _invalid_worker_response("worker resource admission usable_bytes is invalid")
    assumptions = payload.get("assumptions")
    if (
        not isinstance(assumptions, list)
        or not 1 <= len(assumptions) <= 16
        or any(not isinstance(item, str) or not item or len(item) > 256 for item in assumptions)
    ):
        raise _invalid_worker_response("worker resource admission assumptions are invalid")
    if payload.get("reason_code") not in {None, "vram_admission_admitted"}:
        raise _invalid_worker_response("worker resource admission reason is invalid")


def _validate_checkpoint_event_payload(payload: Mapping[str, Any]) -> None:
    step = payload.get("step")
    if isinstance(step, bool) or not isinstance(step, int) or step < 0:
        raise _invalid_worker_response("worker checkpoint event step is invalid")
    _artifact_name(payload.get("name"))
    digest = payload.get("sha256")
    if digest is not None and (not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
        raise _invalid_worker_response("worker checkpoint event hash is invalid")


def _validate_progress_event_payload(payload: Mapping[str, Any]) -> None:
    step = payload.get("step")
    max_steps = payload.get("max_steps")
    if (
        isinstance(step, bool)
        or not isinstance(step, int)
        or isinstance(max_steps, bool)
        or not isinstance(max_steps, int)
        or not 0 <= step <= max_steps
        or max_steps < 1
    ):
        raise _invalid_worker_response("worker progress event bounds are invalid")
    for key in (
        "epoch",
        "loss",
        "eval_loss",
        "learning_rate",
        "tokens_per_second",
        "gpu_utilization_percent",
        "vram_used_bytes",
    ):
        number = payload.get(key)
        if number is not None and (isinstance(number, bool) or not isinstance(number, (int, float))):
            raise _invalid_worker_response(f"worker progress event {key} is invalid")
    telemetry = payload.get("telemetry")
    if telemetry is None:
        return
    if not isinstance(telemetry, Mapping):
        raise _invalid_worker_response("worker progress telemetry is invalid")
    try:
        validate_progress_telemetry(telemetry)
    except UnslothWorkerCapabilityContractError as exc:
        raise _invalid_worker_response("worker progress telemetry is invalid") from exc


_EVENT_PAYLOAD_VALIDATORS: dict[str, Callable[[Mapping[str, Any]], None]] = {
    "accepted": _validate_accepted_event_payload,
    "artifact": _validate_artifact_metadata,
    "checkpoint": _validate_checkpoint_event_payload,
    "phase": _validate_phase_event_payload,
    "progress": _validate_progress_event_payload,
    "resource_admission": _validate_resource_admission_event_payload,
    "status": _validate_status_event_payload,
}


def _project_worker_event(event: Mapping[str, Any]) -> dict[str, Any]:
    _validate_worker_event(event)
    payload = dict(event.get("payload") or {}) if isinstance(event.get("payload"), Mapping) else {}
    event_type = str(event.get("type") or "progress")
    projected: dict[str, Any] = {"event_id": event.get("sequence"), "type": event_type}
    if event_type == "progress":
        step = int(payload.get("step") or 0)
        max_steps = int(payload.get("max_steps") or 0)
        projected.update(
            {
                "current_step": step,
                "max_steps": max_steps,
                "progress_percent": round(step / max_steps * 100, 4) if max_steps else 0,
                "epoch": payload.get("epoch"),
                "train_loss": payload.get("loss"),
                "eval_loss": payload.get("eval_loss"),
                "learning_rate": payload.get("learning_rate"),
                "telemetry": (
                    validate_progress_telemetry(payload["telemetry"])
                    if isinstance(payload.get("telemetry"), Mapping)
                    else progress_telemetry(payload)
                ),
                "phase": "training",
            }
        )
        for metric_name, state in projected["telemetry"].items():
            if state["status"] == "available":
                projected[metric_name] = state["value"]
    elif event_type == "phase":
        projected["phase"] = payload.get("phase")
        if payload.get("step") is not None:
            projected["current_step"] = payload.get("step")
    elif event_type == "status":
        projected.update(
            {
                "status": payload.get("status"),
                "reason_code": payload.get("reason_code"),
                "retryable": payload.get("retryable"),
            }
        )
    elif event_type == "checkpoint":
        projected.update({"current_step": payload.get("step"), "checkpoint_ref": payload.get("name")})
    return projected


def _validate_worker_correlation(
    payload: Mapping[str, Any],
    *,
    job_id: str,
    attempt_id: str,
    fencing_token: int | None,
    correlation_id: str | None,
) -> None:
    if str(payload.get("contract_version") or "") != WORKER_CONTRACT_VERSION:
        raise MlInternTrainingWorkerTransportError(
            "worker_contract_mismatch",
            "worker response uses an unexpected contract version",
            retryable=False,
        )
    if str(payload.get("job_id") or "") != job_id or str(payload.get("attempt_id") or "") != attempt_id:
        raise MlInternTrainingWorkerTransportError(
            "worker_correlation_mismatch",
            "worker response does not match the admitted job attempt",
            retryable=False,
        )
    if fencing_token is not None and int(payload.get("fencing_token") or 0) != fencing_token:
        raise MlInternTrainingWorkerTransportError(
            "stale_worker_fence",
            "worker response carries a stale fencing token",
            retryable=False,
        )
    if correlation_id is not None and str(payload.get("correlation_id") or "") != correlation_id:
        raise MlInternTrainingWorkerTransportError(
            "worker_correlation_mismatch",
            "worker response does not match the admitted correlation ID",
            retryable=False,
        )


def _existing_root(value: str | Path, name: str) -> Path:
    path = Path(value).resolve()
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f"{name} must be an existing non-symlink directory")
    return path


def _writable_root(value: str | Path, name: str) -> Path:
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True, mode=0o750)
    return _existing_root(path, name)


def _safe_relative(value: str, name: str) -> str:
    candidate = Path(value)
    if not value or candidate.is_absolute() or ".." in candidate.parts or "\\" in value:
        raise ValueError(f"{name} must be a safe relative path")
    return candidate.as_posix()


def _identifier(value: str, name: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise MlInternTrainingWorkerTransportError("invalid_identifier", f"{name} is invalid", retryable=False)
    return normalized


def _artifact_name(value: Any) -> str:
    normalized = str(value or "").strip()
    if (
        not _ARTIFACT_NAME.fullmatch(normalized)
        or normalized.startswith("/")
        or ".." in normalized.split("/")
        or "//" in normalized
    ):
        raise MlInternTrainingWorkerTransportError(
            "invalid_worker_response",
            "worker artifact name is invalid",
            retryable=False,
        )
    return normalized


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_sha256(path: Path) -> str:
    if path.is_symlink():
        raise MlInternTrainingWorkerTransportError(
            "adapter_artifact_invalid", "adapter artifact contains a symbolic link", retryable=False
        )
    if path.is_file():
        return _file_sha256(path)
    if not path.is_dir():
        raise MlInternTrainingWorkerTransportError(
            "adapter_artifact_invalid", "adapter artifact is not a regular tree", retryable=False
        )
    entries = list(path.rglob("*"))
    if any(item.is_symlink() for item in entries):
        raise MlInternTrainingWorkerTransportError(
            "adapter_artifact_invalid", "adapter artifact contains a symbolic link", retryable=False
        )
    if any(not item.is_file() and not item.is_dir() for item in entries):
        raise MlInternTrainingWorkerTransportError(
            "adapter_artifact_invalid", "adapter artifact contains an unsupported entry", retryable=False
        )
    children = sorted(item for item in entries if item.is_file())
    if not children:
        raise MlInternTrainingWorkerTransportError(
            "adapter_artifact_invalid", "adapter artifact tree is empty", retryable=False
        )
    digest = hashlib.sha256()
    for child in children:
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(_file_sha256(child).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _jsonl_record_count(path: Path) -> int:
    count = 0
    with path.open("rb") as handle:
        for line in handle:
            if line.strip():
                count += 1
                if count > 10_000_000:
                    raise MlInternTrainingWorkerTransportError("dataset_too_large", "dataset exceeds record limit")
    if count < 1:
        raise MlInternTrainingWorkerTransportError("dataset_empty", "dataset split is empty", retryable=False)
    return count
