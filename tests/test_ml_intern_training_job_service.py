"""Tests fuer ml_intern_training_job_service (MLLORA-011..014/023)."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent.services.ml_intern_training_job_service import (
    MlInternTrainingJobService,
    get_training_job_service,
)


def _svc(tmp_path: Path, enabled: bool = False, mode: str = "dry_run", backend: str = "mock"):
    cfg = {
        "enabled": enabled,
        "mode": mode,
        "backend": backend,
        "artifact_root": str(tmp_path / "artifacts"),
        "dataset_root": str(tmp_path / "datasets"),
        "timeout_seconds": 60,
        "gpu_profile": "rtx3080-safe",
        "require_dataset_validation": False,
        "require_secret_scan": False,
    }
    return MlInternTrainingJobService(training_config=cfg)


def _write_dataset(tmp_path: Path, name="train.jsonl"):
    d = tmp_path / "datasets"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text('{"instruction": "Hi", "output": "Hello"}\n', encoding="utf-8")
    return name


def test_disabled_returns_disabled_status(tmp_path):
    svc = _svc(tmp_path, enabled=False)
    result = svc.submit_job({"job_type": "dataset_validate"})
    assert result.status == "disabled"
    assert "disabled" in result.errors[0]


def test_dry_run_dataset_validate(tmp_path):
    name = _write_dataset(tmp_path)
    svc = _svc(tmp_path, enabled=True, mode="dry_run")
    result = svc.submit_job({"job_type": "dataset_validate", "dataset_path": name})
    assert result.status == "dry_run_completed"
    assert result.artifact_dir is not None


def test_dry_run_train_lora(tmp_path):
    name = _write_dataset(tmp_path)
    svc = _svc(tmp_path, enabled=True, mode="dry_run")
    result = svc.submit_job({
        "job_type": "train_lora",
        "base_model": "qwen2.5-coder-7b",
        "dataset_path": name,
        "output_dir": "adapter_out",
    })
    assert result.status == "dry_run_completed"
    assert result.job_type == "train_lora"


def test_dry_run_creates_artifacts(tmp_path):
    svc = _svc(tmp_path, enabled=True, mode="dry_run")
    result = svc.submit_job({"job_type": "dataset_validate", "dataset_path": "train.jsonl"})
    assert result.artifact_dir is not None
    artifact_dir = Path(result.artifact_dir)
    assert (artifact_dir / "training_summary.json").exists()
    assert (artifact_dir / "status.json").exists()
    status_data = json.loads((artifact_dir / "status.json").read_text())
    assert status_data["status"] == "dry_run_completed"


def test_dry_run_never_sets_approved_status(tmp_path):
    svc = _svc(tmp_path, enabled=True, mode="dry_run")
    result = svc.submit_job({"job_type": "train_lora", "base_model": "x", "dataset_path": "d.jsonl", "output_dir": "out"})
    assert result.status not in ("approved", "trained")
    assert result.status == "dry_run_completed"


def test_unknown_job_type_rejected(tmp_path):
    svc = _svc(tmp_path, enabled=True)
    result = svc.submit_job({"job_type": "superlern_lora"})
    assert result.status == "validation_failed"
    assert result.errors


def test_merge_without_allow_merge_rejected(tmp_path):
    svc = _svc(tmp_path, enabled=True, mode="live")
    result = svc.submit_job({
        "job_type": "merge_adapter_optional",
        "base_model": "x",
        "adapter_path": "/tmp/adapter",
        "output_dir": "merged",
    })
    assert result.status in ("validation_failed", "failed")
    assert any("allow_merge" in e for e in result.errors)


def test_output_dir_path_traversal_blocked(tmp_path):
    svc = _svc(tmp_path, enabled=True, mode="dry_run")
    result = svc.submit_job({
        "job_type": "train_lora",
        "base_model": "x",
        "dataset_path": "train.jsonl",
        "output_dir": "../../etc",
    })
    assert result.status == "validation_failed"
    assert any("escapes" in e for e in result.errors)


def test_live_dataset_validate(tmp_path):
    name = _write_dataset(tmp_path)
    svc = _svc(tmp_path, enabled=True, mode="live")
    result = svc.submit_job({"job_type": "dataset_validate", "dataset_path": name})
    assert result.status in ("completed", "failed")


def test_live_train_lora_mock_backend(tmp_path):
    name = _write_dataset(tmp_path)
    svc = _svc(tmp_path, enabled=True, mode="live", backend="mock")
    result = svc.submit_job({
        "job_type": "train_lora",
        "base_model": "qwen2.5-coder-7b",
        "dataset_path": name,
        "output_dir": "out",
    })
    assert result.status == "trained"
    assert any("mock" in w for w in result.warnings)


def test_riskig_batch_size_warns(tmp_path):
    svc = _svc(tmp_path, enabled=True, mode="dry_run")
    result = svc.submit_job({
        "job_type": "train_lora",
        "base_model": "x",
        "dataset_path": "d.jsonl",
        "output_dir": "out",
        "batch_size": 100,  # Weit ueber rtx3080-safe limit
    })
    assert any("batch_size" in w for w in result.warnings)


def test_risky_override_documented_in_report(tmp_path):
    svc = _svc(tmp_path, enabled=True, mode="dry_run")
    result = svc.submit_job({
        "job_type": "train_lora",
        "base_model": "x",
        "dataset_path": "d.jsonl",
        "output_dir": "out",
        "batch_size": 100,
        "explicit_override": {"reason": "testing large batch on workstation with 40GB VRAM", "overrides": {"batch_size": 100}},
    })
    # Override-Reason landet in den Warnings
    assert any("override" in w.lower() for w in result.warnings)


def test_training_config_separate_from_spike_config(tmp_path):
    """SGPT-Execution-Config und Training-Config sind getrennte Services."""
    from agent.services.ml_intern_spike_config_service import normalize_ml_intern_spike_config
    from agent.services.ml_intern_training_config_service import normalize_ml_intern_training_config
    spike = normalize_ml_intern_spike_config({"enabled": True})
    training = normalize_ml_intern_training_config({})
    assert "command_template" in spike
    assert "artifact_root" in training
    assert "artifact_root" not in spike
    assert "command_template" not in training


def test_failed_status_with_simulated_oom(tmp_path):
    """Simulierter OOM -> status=failed, kein approved."""
    name = _write_dataset(tmp_path)
    svc = _svc(tmp_path, enabled=True, mode="live", backend="mock")
    # Mock den Backend-Runner um OOM zu simulieren
    with patch.object(svc, "_invoke_backend_runner", return_value={"status": "failed", "errors": ["CUDA out of memory"], "warnings": []}):
        result = svc.submit_job({
            "job_type": "train_lora",
            "base_model": "x",
            "dataset_path": name,
            "output_dir": "out",
        })
    assert result.status == "failed"
    assert any("out of memory" in e for e in result.errors)
