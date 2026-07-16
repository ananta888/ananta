"""Training-Config-Normalisierung fuer den ml_intern LoRA/QLoRA Fine-Tuning-Pfad.

Getrennt von ml_intern_spike_config_service — Training-Limits sind groesser als
Prompt-Execution-Limits, aber genau so bounded und default-disabled.
"""

from __future__ import annotations

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
_ALLOWED_BACKENDS = frozenset({"unsloth", "peft_trl", "mock"})
_ALLOWED_GPU_PROFILES = frozenset({"rtx3080-safe", "generic-safe", "none"})

# Sichere Defaults fuer das RTX-3080-Profil
_GPU_PROFILES: dict[str, dict] = {
    "rtx3080-safe": {
        "load_in_4bit": True,
        "lora_rank": 16,
        "lora_alpha": 32,
        "lora_dropout": 0.05,
        "max_seq_length": 2048,
        "batch_size": 2,
        "gradient_accumulation_steps": 4,
        "learning_rate": 2e-4,
        "max_batch_size_hard_limit": 8,
        "max_seq_length_hard_limit": 4096,
    },
    "generic-safe": {
        "load_in_4bit": True,
        "lora_rank": 8,
        "lora_alpha": 16,
        "lora_dropout": 0.05,
        "max_seq_length": 1024,
        "batch_size": 1,
        "gradient_accumulation_steps": 8,
        "learning_rate": 2e-4,
        "max_batch_size_hard_limit": 4,
        "max_seq_length_hard_limit": 2048,
    },
    "none": {
        "load_in_4bit": False,
        "lora_rank": 8,
        "lora_alpha": 16,
        "lora_dropout": 0.05,
        "max_seq_length": 512,
        "batch_size": 1,
        "gradient_accumulation_steps": 1,
        "learning_rate": 2e-4,
        "max_batch_size_hard_limit": 2,
        "max_seq_length_hard_limit": 512,
    },
}

_ENV_ALLOWLIST_DEFAULTS = ["HOME", "PATH", "CUDA_VISIBLE_DEVICES", "HF_HOME", "TRANSFORMERS_CACHE"]


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
        "mode": mode,
        "backend": backend,
        "allowed_job_types": allowed_job_types,
        "artifact_root": artifact_root,
        "dataset_root": dataset_root,
        "timeout_seconds": timeout_seconds,
        "max_dataset_bytes": max_dataset_bytes,
        "max_adapter_bytes": max_adapter_bytes,
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
