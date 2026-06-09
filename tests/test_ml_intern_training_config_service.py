"""Tests fuer ml_intern_training_config_service (MLLORA-004/023)."""

from agent.services.ml_intern_training_config_service import (
    normalize_ml_intern_training_config,
    normalize_lora_runtime_config,
    get_gpu_profile_defaults,
)


def test_defaults_are_safe():
    cfg = normalize_ml_intern_training_config(None)
    assert cfg["enabled"] is False
    assert cfg["mode"] == "dry_run"
    assert cfg["auto_activate_adapter"] is False
    assert cfg["require_dataset_validation"] is True
    assert cfg["require_secret_scan"] is True
    assert cfg["require_eval_before_approval"] is True
    assert cfg["external_network_allowed"] is False


def test_enabled_can_be_set():
    cfg = normalize_ml_intern_training_config({"enabled": True, "mode": "live"})
    assert cfg["enabled"] is True
    assert cfg["mode"] == "live"


def test_invalid_mode_falls_back_to_dry_run():
    cfg = normalize_ml_intern_training_config({"mode": "superlive"})
    assert cfg["mode"] == "dry_run"


def test_invalid_backend_falls_back():
    cfg = normalize_ml_intern_training_config({"backend": "jupyter_notebook"})
    assert cfg["backend"] == "unsloth"


def test_timeout_is_bounded():
    cfg_low = normalize_ml_intern_training_config({"timeout_seconds": 1})
    assert cfg_low["timeout_seconds"] == 60
    cfg_high = normalize_ml_intern_training_config({"timeout_seconds": 999999})
    assert cfg_high["timeout_seconds"] == 86400


def test_max_dataset_bytes_bounded():
    cfg = normalize_ml_intern_training_config({"max_dataset_bytes": 0})
    assert cfg["max_dataset_bytes"] >= 1024
    cfg2 = normalize_ml_intern_training_config({"max_dataset_bytes": 10**12})
    assert cfg2["max_dataset_bytes"] <= 10 * 1024 * 1024 * 1024


def test_gpu_profile_defaults_rtx3080():
    cfg = normalize_ml_intern_training_config({"gpu_profile": "rtx3080-safe"})
    gp = cfg["gpu_profile_defaults"]
    assert gp["load_in_4bit"] is True
    assert gp["lora_rank"] == 16
    assert gp["batch_size"] == 2
    assert gp["max_seq_length"] == 2048


def test_unknown_gpu_profile_falls_back():
    cfg = normalize_ml_intern_training_config({"gpu_profile": "titan-unlimited"})
    assert cfg["gpu_profile"] == "rtx3080-safe"


def test_env_allowlist_deduplication():
    cfg = normalize_ml_intern_training_config({"env_allowlist": ["HOME", "HOME", "PATH"]})
    assert cfg["env_allowlist"].count("HOME") == 1


def test_allowed_job_types_filtered():
    cfg = normalize_ml_intern_training_config({"allowed_job_types": ["train_lora", "imaginary_job"]})
    assert "train_lora" in cfg["allowed_job_types"]
    assert "imaginary_job" not in cfg["allowed_job_types"]


def test_spike_config_unchanged():
    """Bestehende ml_intern_spike Config bleibt von Training-Config getrennt."""
    from agent.services.ml_intern_spike_config_service import normalize_ml_intern_spike_config
    spike = normalize_ml_intern_spike_config({"enabled": True, "command_template": "echo hi"})
    assert spike["enabled"] is True
    training = normalize_ml_intern_training_config({})
    assert training["enabled"] is False  # Getrennte Defaults


def test_lora_runtime_defaults():
    rt = normalize_lora_runtime_config(None)
    assert rt["enabled"] is False
    assert rt["routing_enabled"] is False
    assert rt["approved_only"] is True
    assert rt["fallback_to_base_model"] is True


def test_get_gpu_profile_defaults_returns_dict():
    p = get_gpu_profile_defaults("rtx3080-safe")
    assert isinstance(p, dict)
    assert "max_seq_length_hard_limit" in p
