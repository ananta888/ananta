"""Training-Config-Normalisierung fuer den ml_intern LoRA/QLoRA Fine-Tuning-Pfad.

Getrennt von ml_intern_spike_config_service — Training-Limits sind groesser als
Prompt-Execution-Limits, aber genau so bounded und default-disabled.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from agent.services.ml_intern_training_contract import (
    UNSLOTH_BACKENDS,
    MlInternTrainingContractError,
    normalize_run_ids,
    normalize_source_ids,
)
from ananta_contracts.unsloth_capability import hub_gpu_profile_defaults

_ALLOWED_JOB_TYPES = frozenset(
    {
        "dataset_validate",
        "train_lora",
        "evaluate_lora",
        "register_adapter",
        "export_adapter",
        "merge_adapter_optional",
    }
)
_DEFAULT_JOB_TYPES = _ALLOWED_JOB_TYPES.difference({"merge_adapter_optional"})

_ALLOWED_MODES = frozenset({"dry_run", "live"})
_ALLOWED_BACKENDS = frozenset(
    {
        "autotrain",
        "axolotl",
        "llamafactory",
        "peft_trl",
        "needle",
        "mock",
        "torchtune",
        *UNSLOTH_BACKENDS,
    }
)
_ALLOWED_GPU_PROFILES = frozenset({"rtx3080-safe", "generic-safe", "none"})
_UNSLOTH_OPERATING_MODES = frozenset({"core_worker", "studio_managed", "external_api"})
_UNSLOTH_SECURITY_KEYS = frozenset(
    {
        "operating_mode",
        "model_downloads_enabled",
        "remote_tunnel_enabled",
        "code_execution_enabled",
        "mcp_enabled",
        "local_network_enabled",
        "studio_url",
        "auth_secret_ref",
        "mcp_auth_secret_ref",
        "expected_studio_version",
        "tls_required",
        "allowed_hosts",
        "allowed_ip_cidrs",
        "require_grounded_provenance",
        "trusted_source_ids",
        "trusted_run_ids",
    }
)
_ENV_SECRET_REF_RE = re.compile(r"^env://[A-Za-z_][A-Za-z0-9_]{0,127}$")

_GPU_PROFILES: dict[str, dict] = {
    profile: hub_gpu_profile_defaults(profile) for profile in ("rtx3080-safe", "generic-safe", "none")
}

_ENV_ALLOWLIST_DEFAULTS = ["HOME", "PATH", "CUDA_VISIBLE_DEVICES", "HF_HOME", "TRANSFORMERS_CACHE"]


class MlInternTrainingConfigError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def normalize_unsloth_security_config(
    value: Mapping[str, Any] | None,
    *,
    integration_enabled: bool = False,
    external_network_allowed: bool = False,
) -> dict[str, Any]:
    """Validate new Unsloth security controls without weakening legacy defaults."""

    if value is None:
        payload: dict[str, Any] = {}
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise MlInternTrainingConfigError(
            "unsloth_security_invalid",
            "unsloth_security must be an object",
        )
    unknown = sorted(set(payload) - _UNSLOTH_SECURITY_KEYS)
    if unknown:
        raise MlInternTrainingConfigError(
            "unsloth_security_unknown_fields",
            f"unknown Unsloth security fields: {', '.join(unknown[:10])}",
        )

    operating_mode = str(payload.get("operating_mode") or "core_worker").strip().lower()
    if operating_mode not in _UNSLOTH_OPERATING_MODES:
        raise MlInternTrainingConfigError(
            "unsloth_operating_mode_invalid",
            "Unsloth operating_mode is not supported",
        )

    flags = {
        key: _strict_security_bool(payload, key, default=False)
        for key in (
            "model_downloads_enabled",
            "remote_tunnel_enabled",
            "code_execution_enabled",
            "mcp_enabled",
            "local_network_enabled",
            "require_grounded_provenance",
        )
    }
    tls_required = _strict_security_bool(payload, "tls_required", default=True)
    allowed_hosts = _normalize_allowed_hosts(payload.get("allowed_hosts"))
    allowed_ip_cidrs = _normalize_unsloth_cidrs(payload.get("allowed_ip_cidrs"))
    studio_url = _normalize_studio_url(payload.get("studio_url"))
    auth_secret_ref = _normalize_secret_ref(payload.get("auth_secret_ref"))
    mcp_auth_secret_ref = _normalize_secret_ref(payload.get("mcp_auth_secret_ref"))
    expected_studio_version = _normalize_unsloth_version(payload.get("expected_studio_version"))

    remote_mode = operating_mode in {"studio_managed", "external_api"}
    if operating_mode == "core_worker" and (
        flags["remote_tunnel_enabled"]
        or flags["code_execution_enabled"]
        or flags["mcp_enabled"]
        or flags["local_network_enabled"]
        or studio_url is not None
        or auth_secret_ref is not None
        or mcp_auth_secret_ref is not None
        or bool(allowed_hosts)
        or bool(allowed_ip_cidrs)
        or expected_studio_version is not None
        or ("tls_required" in payload and tls_required is not True)
    ):
        raise MlInternTrainingConfigError(
            "unsloth_mode_conflict",
            "core_worker cannot activate Studio, MCP, tunnels or remote code execution",
        )
    if (
        flags["model_downloads_enabled"]
        or flags["remote_tunnel_enabled"]
        or (integration_enabled and remote_mode and not flags["local_network_enabled"])
    ) and not external_network_allowed:
        raise MlInternTrainingConfigError(
            "unsloth_network_opt_in_required",
            "Unsloth network features require the independent external-network opt-in",
        )
    if integration_enabled and remote_mode:
        if studio_url is None:
            raise MlInternTrainingConfigError(
                "unsloth_studio_url_required",
                "remote Unsloth mode requires a bounded Studio/API URL",
            )
        hostname = str(urlsplit(studio_url).hostname or "").lower()
        if not allowed_hosts or hostname not in allowed_hosts:
            raise MlInternTrainingConfigError(
                "unsloth_host_allowlist_required",
                "remote Unsloth mode requires an exact host allowlist match",
            )
        if auth_secret_ref is None:
            raise MlInternTrainingConfigError(
                "unsloth_auth_secret_ref_required",
                "remote Unsloth mode requires a secret reference",
            )
        if not allowed_ip_cidrs:
            raise MlInternTrainingConfigError(
                "unsloth_ip_allowlist_required",
                "remote Unsloth mode requires an exact IP/CIDR allowlist",
            )
        if expected_studio_version is None:
            raise MlInternTrainingConfigError(
                "unsloth_studio_version_required",
                "remote Unsloth mode requires a pinned Studio version",
            )
        if tls_required and urlsplit(studio_url).scheme != "https":
            raise MlInternTrainingConfigError(
                "unsloth_tls_policy_required",
                "remote Unsloth mode requires HTTPS while tls_required is enabled",
            )
        if flags["local_network_enabled"]:
            if operating_mode != "studio_managed":
                raise MlInternTrainingConfigError(
                    "unsloth_local_network_mode_invalid",
                    "local Studio networking is valid only for studio_managed mode",
                )
            if any(not ipaddress.ip_network(value, strict=True).is_private for value in allowed_ip_cidrs):
                raise MlInternTrainingConfigError(
                    "unsloth_local_network_cidr_invalid",
                    "local Studio networking requires private CIDRs",
                )
        elif not tls_required:
            raise MlInternTrainingConfigError(
                "unsloth_tls_policy_required",
                "plaintext Studio transport is valid only on the explicit local network",
            )
        if flags["mcp_enabled"] and mcp_auth_secret_ref is None:
            raise MlInternTrainingConfigError(
                "unsloth_mcp_auth_secret_ref_required",
                "enabled MCP requires its separate upstream bearer secret reference",
            )

    try:
        trusted_source_ids = normalize_source_ids(payload.get("trusted_source_ids"))
        trusted_run_ids = normalize_run_ids(payload.get("trusted_run_ids"))
    except MlInternTrainingContractError as exc:
        raise MlInternTrainingConfigError(exc.reason_code, str(exc)) from exc

    return {
        "operating_mode": operating_mode,
        **flags,
        "studio_url": studio_url,
        "auth_secret_ref": auth_secret_ref,
        "mcp_auth_secret_ref": mcp_auth_secret_ref,
        "expected_studio_version": expected_studio_version,
        "tls_required": tls_required,
        "allowed_hosts": list(allowed_hosts),
        "allowed_ip_cidrs": list(allowed_ip_cidrs),
        "trusted_source_ids": list(trusted_source_ids),
        "trusted_run_ids": list(trusted_run_ids),
    }


def _normalize_unsloth_cidrs(value: Any) -> tuple[str, ...]:
    if value is None or value == [] or value == ():
        return ()
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 32:
        raise MlInternTrainingConfigError(
            "unsloth_ip_allowlist_invalid",
            "allowed_ip_cidrs must contain 1..32 canonical networks",
        )
    result: list[str] = []
    try:
        for item in value:
            network = ipaddress.ip_network(str(item), strict=True)
            normalized = str(network)
            if normalized in result:
                raise ValueError("duplicate network")
            result.append(normalized)
    except ValueError as exc:
        raise MlInternTrainingConfigError(
            "unsloth_ip_allowlist_invalid",
            "allowed_ip_cidrs contains an invalid or duplicate network",
        ) from exc
    return tuple(result)


def _normalize_unsloth_version(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", normalized):
        raise MlInternTrainingConfigError(
            "unsloth_studio_version_invalid",
            "expected_studio_version is invalid",
        )
    return normalized


def _strict_security_bool(payload: Mapping[str, Any], key: str, *, default: bool) -> bool:
    if key not in payload:
        return default
    value = payload[key]
    if not isinstance(value, bool):
        raise MlInternTrainingConfigError(
            "unsloth_security_flag_invalid",
            f"{key} must be a JSON boolean",
        )
    return value


def _normalize_allowed_hosts(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 64:
        raise MlInternTrainingConfigError(
            "unsloth_host_allowlist_invalid",
            "allowed_hosts must be a bounded array",
        )
    hosts: list[str] = []
    for item in value:
        host = str(item or "").strip().lower()
        if (
            not host
            or len(host) > 253
            or any(character.isspace() for character in host)
            or any(token in host for token in ("://", "/", "@"))
        ):
            raise MlInternTrainingConfigError(
                "unsloth_host_allowlist_invalid",
                "allowed_hosts contains an invalid exact hostname",
            )
        if host not in hosts:
            hosts.append(host)
    return tuple(sorted(hosts))


def _normalize_studio_url(value: Any) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if len(normalized) > 2048:
        raise MlInternTrainingConfigError("unsloth_studio_url_invalid", "Studio URL exceeds its bound")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise MlInternTrainingConfigError(
            "unsloth_studio_url_invalid",
            "Studio URL must be an absolute HTTP(S) URL without credentials or fragments",
        )
    return normalized.rstrip("/")


def _normalize_secret_ref(value: Any) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    file_reference = normalized.startswith("file:///") and len(normalized) > len("file:///")
    if len(normalized) > 512 or (not _ENV_SECRET_REF_RE.fullmatch(normalized) and not file_reference):
        raise MlInternTrainingConfigError(
            "unsloth_secret_reference_invalid",
            "Unsloth credentials must use an env:// or absolute file:/// secret reference",
        )
    return normalized


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _bounded_float(value: object, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def normalize_ml_intern_training_config(value: dict | None) -> dict:
    """Normalisiert und bounded die ml_intern_training Config-Gruppe.

    Felder aus value werden normalisiert; unbekannte Felder werden ignoriert.
    Gibt immer ein vollstaendiges, sicheres Config-Dict zurueck.
    """
    payload = dict(value or {})

    enabled = bool(payload.get("enabled", False))
    unsloth_integration_enabled = payload.get(
        "unsloth_integration_enabled",
        False,
    )
    if not isinstance(unsloth_integration_enabled, bool):
        raise MlInternTrainingConfigError("unsloth_integration_flag_invalid")
    mode = str(payload.get("mode") or "dry_run").strip().lower()
    if mode not in _ALLOWED_MODES:
        mode = "dry_run"

    backend = str(payload.get("backend") or "unsloth").strip().lower()
    if backend not in _ALLOWED_BACKENDS:
        backend = "unsloth"

    raw_job_types = payload.get("allowed_job_types")
    if isinstance(raw_job_types, list):
        allowed_job_types = sorted(
            {str(jt).strip().lower() for jt in raw_job_types if str(jt or "").strip().lower() in _ALLOWED_JOB_TYPES}
        )
    else:
        allowed_job_types = sorted(_DEFAULT_JOB_TYPES)

    artifact_root = str(payload.get("artifact_root") or "artifacts/lora").strip()
    dataset_root = str(payload.get("dataset_root") or "data/training/lora").strip()

    timeout_seconds = _bounded_int(payload.get("timeout_seconds", 3600), default=3600, minimum=60, maximum=86400)
    max_dataset_bytes = _bounded_int(
        payload.get("max_dataset_bytes", 104857600),
        default=104857600,
        minimum=1024,
        maximum=10 * 1024 * 1024 * 1024,
    )
    max_adapter_bytes = _bounded_int(
        payload.get("max_adapter_bytes", 2 * 1024 * 1024 * 1024),
        default=2 * 1024 * 1024 * 1024,
        minimum=1024,
        maximum=20 * 1024 * 1024 * 1024,
    )
    max_model_bytes = _bounded_int(
        payload.get("max_model_bytes", 20 * 1024 * 1024 * 1024),
        default=20 * 1024 * 1024 * 1024,
        minimum=1024,
        maximum=100 * 1024 * 1024 * 1024,
    )
    max_checkpoint_bytes = _bounded_int(
        payload.get("max_checkpoint_bytes", 8 * 1024 * 1024 * 1024),
        default=8 * 1024 * 1024 * 1024,
        minimum=1024,
        maximum=100 * 1024 * 1024 * 1024,
    )
    max_export_bytes = _bounded_int(
        payload.get("max_export_bytes", 20 * 1024 * 1024 * 1024),
        default=20 * 1024 * 1024 * 1024,
        minimum=1024,
        maximum=100 * 1024 * 1024 * 1024,
    )
    max_tenant_storage_bytes = max(
        max_dataset_bytes,
        max_adapter_bytes,
        max_model_bytes,
        max_checkpoint_bytes,
        max_export_bytes,
        _bounded_int(
            payload.get("max_tenant_storage_bytes", 64 * 1024 * 1024 * 1024),
            default=64 * 1024 * 1024 * 1024,
            minimum=1024,
            maximum=500 * 1024 * 1024 * 1024,
        ),
    )
    storage_retention_seconds = _bounded_int(
        payload.get("storage_retention_seconds", 30 * 24 * 60 * 60),
        default=30 * 24 * 60 * 60,
        minimum=60 * 60,
        maximum=366 * 24 * 60 * 60,
    )
    max_cleanup_items = _bounded_int(
        payload.get("max_cleanup_items", 128),
        default=128,
        minimum=1,
        maximum=1024,
    )
    max_concurrent_jobs = _bounded_int(payload.get("max_concurrent_jobs", 1), default=1, minimum=1, maximum=16)
    max_queued_jobs = _bounded_int(payload.get("max_queued_jobs", 32), default=32, minimum=0, maximum=10_000)
    max_preview_records = _bounded_int(payload.get("max_preview_records", 100), default=100, minimum=1, maximum=500)
    split_seed = _bounded_int(payload.get("split_seed", 42), default=42, minimum=0, maximum=2**31 - 1)
    validation_ratio = _bounded_float(payload.get("validation_ratio", 0.1), default=0.1, minimum=0.05, maximum=0.5)
    minimum_eval_score = _bounded_float(
        payload.get("minimum_eval_score", 0.0),
        default=0.0,
        minimum=0.0,
        maximum=1_000_000.0,
    )

    require_dataset_validation = bool(payload.get("require_dataset_validation", True))
    require_secret_scan = bool(payload.get("require_secret_scan", True))
    require_eval_before_approval = bool(payload.get("require_eval_before_approval", True))
    auto_activate_adapter = bool(payload.get("auto_activate_adapter", False))
    external_network_allowed = bool(payload.get("external_network_allowed", False))
    unsloth_security = normalize_unsloth_security_config(
        payload.get("unsloth_security"),
        integration_enabled=unsloth_integration_enabled,
        external_network_allowed=external_network_allowed,
    )

    gpu_profile = str(payload.get("gpu_profile") or "rtx3080-safe").strip().lower()
    if gpu_profile not in _ALLOWED_GPU_PROFILES:
        gpu_profile = "rtx3080-safe"
    gpu_profile_defaults = dict(_GPU_PROFILES.get(gpu_profile, _GPU_PROFILES["rtx3080-safe"]))

    raw_env = payload.get("env_allowlist")
    if isinstance(raw_env, list):
        env_allowlist = sorted({str(k or "").strip() for k in raw_env if str(k or "").strip()})
    else:
        env_allowlist = sorted(set(_ENV_ALLOWLIST_DEFAULTS))

    raw_base_models = payload.get("base_models")
    base_models = []
    if isinstance(raw_base_models, list):
        for value in raw_base_models[:128]:
            model_id = str(value or "").strip()
            if model_id and len(model_id) <= 256 and model_id not in base_models:
                base_models.append(model_id)

    return {
        "enabled": enabled,
        "unsloth_integration_enabled": (unsloth_integration_enabled),
        "mode": mode,
        "backend": backend,
        "allowed_job_types": allowed_job_types,
        "artifact_root": artifact_root,
        "dataset_root": dataset_root,
        "timeout_seconds": timeout_seconds,
        "max_dataset_bytes": max_dataset_bytes,
        "max_adapter_bytes": max_adapter_bytes,
        "max_model_bytes": max_model_bytes,
        "max_checkpoint_bytes": max_checkpoint_bytes,
        "max_export_bytes": max_export_bytes,
        "max_tenant_storage_bytes": max_tenant_storage_bytes,
        "storage_retention_seconds": storage_retention_seconds,
        "max_cleanup_items": max_cleanup_items,
        "max_concurrent_jobs": max_concurrent_jobs,
        "max_queued_jobs": max_queued_jobs,
        "max_preview_records": max_preview_records,
        "split_seed": split_seed,
        "validation_ratio": validation_ratio,
        "minimum_eval_score": minimum_eval_score,
        "base_models": base_models,
        "require_dataset_validation": require_dataset_validation,
        "require_secret_scan": require_secret_scan,
        "require_eval_before_approval": require_eval_before_approval,
        "auto_activate_adapter": auto_activate_adapter,
        "external_network_allowed": external_network_allowed,
        "unsloth_security": unsloth_security,
        "gpu_profile": gpu_profile,
        "gpu_profile_defaults": gpu_profile_defaults,
        "env_allowlist": env_allowlist,
    }


def normalize_lora_runtime_config(value: dict | None) -> dict:
    """Normalisiert die lora_runtime Config-Gruppe (optionales Adapter-Routing)."""
    payload = dict(value or {})
    return {
        "enabled": bool(payload.get("enabled", False)),
        "adapter_registry_path": str(
            payload.get("adapter_registry_path") or "artifacts/lora/adapter_registry.json"
        ).strip(),
        "routing_enabled": bool(payload.get("routing_enabled", False)),
        "fallback_to_base_model": bool(payload.get("fallback_to_base_model", True)),
        "approved_only": bool(payload.get("approved_only", True)),
    }


def get_gpu_profile_defaults(profile_name: str) -> dict:
    """Gibt sichere Defaults fuer ein GPU-Profil zurueck."""
    return dict(_GPU_PROFILES.get(profile_name, _GPU_PROFILES["rtx3080-safe"]))
