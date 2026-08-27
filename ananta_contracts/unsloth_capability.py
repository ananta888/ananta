"""Versioned, dependency-free Unsloth Worker capability and telemetry contract."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

UNSLOTH_WORKER_CAPABILITY_SCHEMA_VERSION = "ananta.unsloth-worker-capabilities.v1"

UNSLOTH_WORKER_REASON_CODES = frozenset(
    {
        "ok",
        "backend_not_configured",
        "composition_not_probed",
        "configuration_invalid",
        "cuda_unavailable",
        "dependency_unavailable",
        "package_unavailable",
        "resource_profile_unavailable",
        "runtime_not_configured",
        "worker_probe_invalid",
        "worker_probe_timeout",
        "worker_unavailable",
    }
)

UNSLOTH_FACET_REASON_CODES = frozenset(
    {
        "live_mode_disabled",
        "training_disabled",
        "unsloth_cuda_capability_unavailable",
        "unsloth_export_disabled",
        "unsloth_inference_capability_unavailable",
        "unsloth_mcp_client_unavailable",
        "unsloth_mcp_disabled",
        "unsloth_studio_client_unavailable",
        "unsloth_studio_disabled",
        "worker_capability_unavailable",
        "worker_profile_unavailable",
        "worker_unavailable",
    }
)

UNSLOTH_BACKEND_MODEL_KINDS: dict[str, tuple[str, ...]] = {
    "mock": ("text",),
    "needle": ("text",),
    "peft_trl": ("text",),
    "unsloth": ("text",),
    "unsloth_vision": ("vision",),
    "unsloth_audio": ("audio",),
    "unsloth_embedding": ("embedding",),
}
UNSLOTH_PACKAGE_IDS = ("unsloth", "unsloth_zoo", "torch")
GPU_PROFILE_IDS = ("rtx3080-safe", "generic-safe", "none")

_GIB = 1024**3
_PROFILE_LIMITS: dict[str, dict[str, Any]] = {
    "rtx3080-safe": {
        "capacity_bytes": 10 * _GIB,
        "reserve_bytes": 1 * _GIB,
        "max_model_weight_bytes": 6 * _GIB,
        "max_sequence_length": 2048,
        "max_train_batch_size": 1,
        "max_eval_batch_size": 1,
        "max_gradient_accumulation_steps": 32,
        "max_lora_rank": 32,
        "max_lora_alpha": 64,
        "max_lora_dropout": 0.1,
        "max_target_modules": 64,
        "required_quantization": "4bit",
    },
    "generic-safe": {
        "capacity_bytes": None,
        "reserve_bytes": 0,
        "max_model_weight_bytes": 64 * _GIB,
        "max_sequence_length": 2048,
        "max_train_batch_size": 4,
        "max_eval_batch_size": 4,
        "max_gradient_accumulation_steps": 128,
        "max_lora_rank": 64,
        "max_lora_alpha": 128,
        "max_lora_dropout": 0.2,
        "max_target_modules": 64,
        "required_quantization": "4bit",
    },
    "none": {
        "capacity_bytes": None,
        "reserve_bytes": 0,
        "max_model_weight_bytes": 16 * _GIB,
        "max_sequence_length": 512,
        "max_train_batch_size": 2,
        "max_eval_batch_size": 2,
        "max_gradient_accumulation_steps": 32,
        "max_lora_rank": 32,
        "max_lora_alpha": 64,
        "max_lora_dropout": 0.2,
        "max_target_modules": 64,
        "required_quantization": "none",
    },
}
_PROFILE_LIMIT_FIELDS = frozenset(
    {
        "profile",
        "capacity_bytes",
        "reserve_bytes",
        "usable_bytes",
        "max_model_weight_bytes",
        "max_sequence_length",
        "max_train_batch_size",
        "max_eval_batch_size",
        "max_gradient_accumulation_steps",
        "max_lora_rank",
        "max_lora_alpha",
        "max_lora_dropout",
        "max_target_modules",
        "required_quantization",
    }
)
_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "contract_version",
        "status",
        "reason_code",
        "resource_profile",
        "backends",
        "packages",
        "hardware",
        "gpu_profiles",
        "compositions",
        "limits",
    }
)
PROGRESS_TELEMETRY_UNITS = {
    "tokens_per_second": "tokens/s",
    "gpu_utilization_percent": "percent",
    "vram_used_bytes": "bytes",
}


class UnslothWorkerCapabilityContractError(ValueError):
    """Raised when a Worker probe is missing or incompatible."""


def worker_gpu_profile_limits(profile: str) -> dict[str, Any]:
    normalized = str(profile or "").strip().lower()
    if normalized not in _PROFILE_LIMITS:
        raise UnslothWorkerCapabilityContractError("unknown GPU admission profile")
    values = dict(_PROFILE_LIMITS[normalized])
    capacity = values["capacity_bytes"]
    values["profile"] = normalized
    values["usable_bytes"] = None if capacity is None else capacity - values["reserve_bytes"]
    return values


def hub_gpu_profile_defaults(profile: str) -> dict[str, Any]:
    """Project Worker admission bounds into the legacy Hub configuration shape."""

    limits = worker_gpu_profile_limits(profile)
    return {
        "load_in_4bit": limits["required_quantization"] == "4bit",
        "lora_rank": min(16, limits["max_lora_rank"]),
        "lora_alpha": min(32, limits["max_lora_alpha"]),
        "lora_dropout": min(0.05, limits["max_lora_dropout"]),
        "max_seq_length": limits["max_sequence_length"],
        "batch_size": limits["max_train_batch_size"],
        "gradient_accumulation_steps": min(8, limits["max_gradient_accumulation_steps"]),
        "learning_rate": 2e-4,
        "max_batch_size_hard_limit": limits["max_train_batch_size"],
        "max_eval_batch_size_hard_limit": limits["max_eval_batch_size"],
        "max_seq_length_hard_limit": limits["max_sequence_length"],
        "max_gradient_accumulation_steps_hard_limit": limits["max_gradient_accumulation_steps"],
        "max_lora_rank_hard_limit": limits["max_lora_rank"],
        "max_lora_alpha_hard_limit": limits["max_lora_alpha"],
        "max_lora_dropout_hard_limit": limits["max_lora_dropout"],
        "max_target_modules_hard_limit": limits["max_target_modules"],
        "max_model_weight_bytes": limits["max_model_weight_bytes"],
        "required_quantization": limits["required_quantization"],
        "capacity_bytes": limits["capacity_bytes"],
        "reserve_bytes": limits["reserve_bytes"],
        "usable_bytes": limits["usable_bytes"],
    }


def compose_worker_capability_probe(
    *,
    contract_version: str,
    resource_profile: str,
    active_gpu_profile: str,
    backend_availability: Mapping[str, tuple[bool, str | None]],
    package_versions: Mapping[str, str],
    hardware: Mapping[str, Any],
    runtime_ready: bool,
    runtime_reason_code: str = "configuration_invalid",
) -> dict[str, Any]:
    """Compose the complete Worker-owned probe; absent data remains explicit."""

    normalized_resource = str(resource_profile or "").strip().lower()
    normalized_profile = str(active_gpu_profile or "").strip().lower()
    if normalized_profile not in GPU_PROFILE_IDS:
        normalized_profile = "none"
        runtime_ready = False
        runtime_reason_code = "configuration_invalid"

    cuda_available = hardware.get("cuda_available") is True
    normalized_hardware = {
        "cuda_available": cuda_available,
        "reason_code": "ok" if cuda_available else "cuda_unavailable",
        "torch_version": _bounded_optional_text(hardware.get("torch_version")),
        "cuda_version": _bounded_optional_text(hardware.get("cuda_version")),
        "device_count": _bounded_nonnegative_int(hardware.get("device_count")),
        "device_name": _bounded_optional_text(hardware.get("device_name")),
        "total_vram_bytes": _bounded_nonnegative_int(hardware.get("total_vram_bytes")),
    }
    packages = {}
    for package_id in UNSLOTH_PACKAGE_IDS:
        version = _bounded_optional_text(package_versions.get(package_id))
        packages[package_id] = {
            "available": version is not None,
            "version": version,
            "reason_code": "ok" if version is not None else "package_unavailable",
        }

    backends = {}
    for backend_id, model_kinds in UNSLOTH_BACKEND_MODEL_KINDS.items():
        configured, detail = backend_availability.get(backend_id, (False, None))
        available = bool(configured)
        reason_code = "ok" if available else ("dependency_unavailable" if detail else "backend_not_configured")
        if backend_id.startswith("unsloth") and (
            not packages["unsloth"]["available"] or normalized_resource != "nvidia" or not cuda_available
        ):
            available = False
            reason_code = "package_unavailable" if not packages["unsloth"]["available"] else "cuda_unavailable"
        backends[backend_id] = {
            "available": available,
            "reason_code": reason_code,
            "variant": backend_id,
            "operations": (
                ["train_lora", "evaluate_lora"] if backend_id in {"mock", "peft_trl", "unsloth"} else ["train_lora"]
            ),
            "model_kinds": list(model_kinds),
        }

    gpu_profiles = {}
    for profile_id in GPU_PROFILE_IDS:
        available = runtime_ready and (
            normalized_resource in {"mock", "cpu"}
            if profile_id == "none"
            else (normalized_resource == "nvidia" and normalized_profile == profile_id and cuda_available)
        )
        gpu_profiles[profile_id] = {
            "available": available,
            "reason_code": "ok" if available else "resource_profile_unavailable",
        }

    probe = {
        "schema_version": UNSLOTH_WORKER_CAPABILITY_SCHEMA_VERSION,
        "contract_version": _bounded_required_text(contract_version, "contract_version"),
        "status": "ready" if runtime_ready else "degraded",
        "reason_code": "ok" if runtime_ready else runtime_reason_code,
        "resource_profile": normalized_resource,
        "backends": backends,
        "packages": packages,
        "hardware": normalized_hardware,
        "gpu_profiles": gpu_profiles,
        "compositions": {
            "studio": {"available": False, "reason_code": "composition_not_probed"},
            "mcp": {"available": False, "reason_code": "composition_not_probed"},
        },
        "limits": worker_gpu_profile_limits(normalized_profile),
    }
    return validate_worker_capability_probe(probe)


def unavailable_worker_capability_probe(
    *,
    contract_version: str,
    resource_profile: str = "mock",
    reason_code: str = "runtime_not_configured",
) -> dict[str, Any]:
    return compose_worker_capability_probe(
        contract_version=contract_version,
        resource_profile=resource_profile,
        active_gpu_profile="none",
        backend_availability={},
        package_versions={},
        hardware={},
        runtime_ready=False,
        runtime_reason_code=reason_code,
    )


def validate_worker_capability_probe(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate all fields and return a detached canonical mapping."""

    data = _closed_mapping(value, _TOP_LEVEL_FIELDS, "worker capability probe")
    if data.get("schema_version") != UNSLOTH_WORKER_CAPABILITY_SCHEMA_VERSION:
        raise UnslothWorkerCapabilityContractError("worker capability schema version is incompatible")
    contract_version = _bounded_required_text(data.get("contract_version"), "contract_version")
    status = data.get("status")
    if status not in {"ready", "degraded"}:
        raise UnslothWorkerCapabilityContractError("worker capability status is invalid")
    reason_code = _reason_code(data.get("reason_code"))
    if (status == "ready") != (reason_code == "ok"):
        raise UnslothWorkerCapabilityContractError("worker capability status and reason disagree")
    resource_profile = data.get("resource_profile")
    if resource_profile not in {"mock", "cpu", "nvidia"}:
        raise UnslothWorkerCapabilityContractError("worker resource profile is invalid")

    backend_data = _closed_mapping(data.get("backends"), frozenset(UNSLOTH_BACKEND_MODEL_KINDS), "worker backends")
    backends = {}
    backend_fields = frozenset({"available", "reason_code", "variant", "operations", "model_kinds"})
    for backend_id, expected_model_kinds in UNSLOTH_BACKEND_MODEL_KINDS.items():
        state = _closed_mapping(backend_data[backend_id], backend_fields, f"backend {backend_id}")
        available = _strict_bool(state.get("available"), f"backend {backend_id}.available")
        state_reason = _reason_code(state.get("reason_code"))
        _validate_availability_reason(available, state_reason, f"backend {backend_id}")
        operations = _bounded_string_list(
            state.get("operations"), {"train_lora", "evaluate_lora"}, f"backend {backend_id}.operations"
        )
        model_kinds = _bounded_string_list(
            state.get("model_kinds"), set(expected_model_kinds), f"backend {backend_id}.model_kinds"
        )
        if set(model_kinds) != set(expected_model_kinds):
            raise UnslothWorkerCapabilityContractError(f"backend {backend_id} model kinds are incomplete")
        backends[backend_id] = {
            "available": available,
            "reason_code": state_reason,
            "variant": _bounded_required_text(state.get("variant"), f"backend {backend_id}.variant"),
            "operations": operations,
            "model_kinds": model_kinds,
        }

    package_data = _closed_mapping(data.get("packages"), frozenset(UNSLOTH_PACKAGE_IDS), "worker packages")
    packages = {}
    package_fields = frozenset({"available", "version", "reason_code"})
    for package_id in UNSLOTH_PACKAGE_IDS:
        state = _closed_mapping(package_data[package_id], package_fields, f"package {package_id}")
        available = _strict_bool(state.get("available"), f"package {package_id}.available")
        version = _bounded_optional_text(state.get("version"))
        state_reason = _reason_code(state.get("reason_code"))
        _validate_availability_reason(available, state_reason, f"package {package_id}")
        if available != (version is not None):
            raise UnslothWorkerCapabilityContractError(f"package {package_id} version is inconsistent")
        packages[package_id] = {
            "available": available,
            "version": version,
            "reason_code": state_reason,
        }

    hardware_fields = frozenset(
        {
            "cuda_available",
            "reason_code",
            "torch_version",
            "cuda_version",
            "device_count",
            "device_name",
            "total_vram_bytes",
        }
    )
    raw_hardware = _closed_mapping(data.get("hardware"), hardware_fields, "worker hardware")
    cuda_available = _strict_bool(raw_hardware.get("cuda_available"), "hardware.cuda_available")
    hardware_reason = _reason_code(raw_hardware.get("reason_code"))
    _validate_availability_reason(cuda_available, hardware_reason, "hardware CUDA")
    hardware = {
        "cuda_available": cuda_available,
        "reason_code": hardware_reason,
        "torch_version": _bounded_optional_text(raw_hardware.get("torch_version")),
        "cuda_version": _bounded_optional_text(raw_hardware.get("cuda_version")),
        "device_count": _bounded_nonnegative_int(raw_hardware.get("device_count")),
        "device_name": _bounded_optional_text(raw_hardware.get("device_name")),
        "total_vram_bytes": _bounded_nonnegative_int(raw_hardware.get("total_vram_bytes")),
    }

    profiles_data = _closed_mapping(data.get("gpu_profiles"), frozenset(GPU_PROFILE_IDS), "worker GPU profiles")
    state_fields = frozenset({"available", "reason_code"})
    profiles = {
        profile_id: _validate_availability_state(profiles_data[profile_id], state_fields, f"GPU profile {profile_id}")
        for profile_id in GPU_PROFILE_IDS
    }
    compositions_data = _closed_mapping(data.get("compositions"), frozenset({"studio", "mcp"}), "worker compositions")
    compositions = {
        composition_id: _validate_availability_state(
            compositions_data[composition_id], state_fields, f"composition {composition_id}"
        )
        for composition_id in ("studio", "mcp")
    }
    limits = _validate_profile_limits(data.get("limits"))
    return {
        "schema_version": UNSLOTH_WORKER_CAPABILITY_SCHEMA_VERSION,
        "contract_version": contract_version,
        "status": status,
        "reason_code": reason_code,
        "resource_profile": resource_profile,
        "backends": backends,
        "packages": packages,
        "hardware": hardware,
        "gpu_profiles": profiles,
        "compositions": compositions,
        "limits": limits,
    }


def progress_telemetry(value: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize optional metrics into explicit available/unavailable states."""

    telemetry = {}
    for field, unit in PROGRESS_TELEMETRY_UNITS.items():
        raw = value.get(field)
        valid = not isinstance(raw, bool) and isinstance(raw, (int, float)) and math.isfinite(float(raw))
        valid = valid and float(raw) >= 0
        if field == "gpu_utilization_percent":
            valid = valid and float(raw) <= 100
        telemetry[field] = {
            "status": "available" if valid else "unavailable",
            "value": int(raw) if valid and field == "vram_used_bytes" else (float(raw) if valid else None),
            "unit": unit,
            "reason_code": None if valid else "metric_unavailable",
        }
    return telemetry


def validate_progress_telemetry(value: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    data = _closed_mapping(value, frozenset(PROGRESS_TELEMETRY_UNITS), "progress telemetry")
    result = {}
    fields = frozenset({"status", "value", "unit", "reason_code"})
    for field, unit in PROGRESS_TELEMETRY_UNITS.items():
        state = _closed_mapping(data[field], fields, f"telemetry {field}")
        status = state.get("status")
        raw = state.get("value")
        reason = state.get("reason_code")
        if status not in {"available", "unavailable"} or state.get("unit") != unit:
            raise UnslothWorkerCapabilityContractError(f"telemetry {field} state is invalid")
        if status == "unavailable":
            if raw is not None or reason != "metric_unavailable":
                raise UnslothWorkerCapabilityContractError(f"telemetry {field} unavailable state is invalid")
        elif (
            reason is not None
            or isinstance(raw, bool)
            or not isinstance(raw, (int, float))
            or not math.isfinite(float(raw))
            or float(raw) < 0
            or (field == "gpu_utilization_percent" and float(raw) > 100)
            or (field == "vram_used_bytes" and not isinstance(raw, int))
        ):
            raise UnslothWorkerCapabilityContractError(f"telemetry {field} value is invalid")
        result[field] = {"status": status, "value": raw, "unit": unit, "reason_code": reason}
    return result


def _validate_availability_state(value: Any, fields: frozenset[str], name: str) -> dict[str, Any]:
    state = _closed_mapping(value, fields, name)
    available = _strict_bool(state.get("available"), f"{name}.available")
    reason = _reason_code(state.get("reason_code"))
    _validate_availability_reason(available, reason, name)
    return {"available": available, "reason_code": reason}


def _validate_profile_limits(value: Any) -> dict[str, Any]:
    data = _closed_mapping(value, _PROFILE_LIMIT_FIELDS, "worker profile limits")
    expected = worker_gpu_profile_limits(str(data.get("profile") or ""))
    if data != expected:
        raise UnslothWorkerCapabilityContractError("worker profile limits are incompatible")
    return expected


def _closed_mapping(value: Any, fields: frozenset[str], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise UnslothWorkerCapabilityContractError(f"{name} fields are missing or incompatible")
    return dict(value)


def _reason_code(value: Any) -> str:
    if not isinstance(value, str) or value not in UNSLOTH_WORKER_REASON_CODES:
        raise UnslothWorkerCapabilityContractError("worker reason_code is invalid")
    return value


def _validate_availability_reason(available: bool, reason_code: str, name: str) -> None:
    if available != (reason_code == "ok"):
        raise UnslothWorkerCapabilityContractError(f"{name} availability and reason disagree")


def _strict_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise UnslothWorkerCapabilityContractError(f"{name} must be boolean")
    return value


def _bounded_required_text(value: Any, name: str) -> str:
    normalized = _bounded_optional_text(value)
    if normalized is None:
        raise UnslothWorkerCapabilityContractError(f"{name} is missing")
    return normalized


def _bounded_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise UnslothWorkerCapabilityContractError("worker text field is invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > 128 or any(not character.isprintable() for character in normalized):
        raise UnslothWorkerCapabilityContractError("worker text field is invalid")
    return normalized


def _bounded_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
        raise UnslothWorkerCapabilityContractError("worker integer field is invalid")
    return value


def _bounded_string_list(value: Any, allowed: set[str], name: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not 1 <= len(value) <= 16:
        raise UnslothWorkerCapabilityContractError(f"{name} is invalid")
    normalized = [str(item) for item in value]
    if len(normalized) != len(set(normalized)) or any(item not in allowed for item in normalized):
        raise UnslothWorkerCapabilityContractError(f"{name} is invalid")
    return normalized
