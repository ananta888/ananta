"""Training-Config-Normalisierung fuer den ml_intern LoRA/QLoRA Fine-Tuning-Pfad.

Getrennt von ml_intern_spike_config_service — Training-Limits sind groesser als
Prompt-Execution-Limits, aber genau so bounded und default-disabled.
"""

from __future__ import annotations

_ALLOWED_JOB_TYPES = frozenset({
    "dataset_validate",
    "train_lora",
    "evaluate_lora",
    "register_adapter",
    "export_adapter",
    "merge_adapter_optional",
})

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
        allowed_job_types = sorted(_ALLOWED_JOB_TYPES)

    artifact_root = str(payload.get("artifact_root") or "artifacts/lora").strip()
    dataset_root = str(payload.get("dataset_root") or "data/training/lora").strip()

    try:
        timeout_seconds = int(payload.get("timeout_seconds", 3600))
    except (TypeError, ValueError):
        timeout_seconds = 3600
    timeout_seconds = max(60, min(timeout_seconds, 86400))  # 1min .. 24h

    try:
        max_dataset_bytes = int(payload.get("max_dataset_bytes", 104857600))
    except (TypeError, ValueError):
        max_dataset_bytes = 104857600
    max_dataset_bytes = max(1024, min(max_dataset_bytes, 10 * 1024 * 1024 * 1024))  # 1 KB .. 10 GB

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
        env_allowlist = sorted({
            str(k or "").strip()
            for k in raw_env
            if str(k or "").strip()
        })
    else:
        env_allowlist = sorted(set(_ENV_ALLOWLIST_DEFAULTS))

    return {
        "enabled": enabled,
        "mode": mode,
        "backend": backend,
        "allowed_job_types": allowed_job_types,
        "artifact_root": artifact_root,
        "dataset_root": dataset_root,
        "timeout_seconds": timeout_seconds,
        "max_dataset_bytes": max_dataset_bytes,
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
        "adapter_registry_path": str(payload.get("adapter_registry_path") or "artifacts/lora/adapter_registry.json").strip(),
        "routing_enabled": bool(payload.get("routing_enabled", False)),
        "fallback_to_base_model": bool(payload.get("fallback_to_base_model", True)),
        "approved_only": bool(payload.get("approved_only", True)),
    }


def get_gpu_profile_defaults(profile_name: str) -> dict:
    """Gibt sichere Defaults fuer ein GPU-Profil zurueck."""
    return dict(_GPU_PROFILES.get(profile_name, _GPU_PROFILES["rtx3080-safe"]))
