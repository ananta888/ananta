from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import scripts.run_lora_training_smoke as smoke_gate
from scripts.lora_training_smoke_release_chain import bounded_worker_diagnostic
from scripts.run_lora_training_smoke import (
    _nvidia_probe,
    _tree_sha256,
    _worker_image_build_input_paths,
    _worker_image_fingerprint,
    run_gate,
)


def test_shell_entrypoint_resolves_the_scripts_package_outside_repository(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["bash", str(repository_root / "scripts/run-lora-training-e2e.sh"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Run the local LoRA training acceptance gate" in result.stdout


def test_gate_records_mock_success_and_never_copies_process_output() -> None:
    calls = 0

    def runner(command):
        nonlocal calls
        count = 77 if calls == 0 else 0
        calls += 1
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"{count} passed\nprivate-secret-marker",
            stderr="",
        )

    report = run_gate(run_mock=True, nvidia_model=None, runner=runner)

    assert report["ok"] is True
    assert report["mock_cpu_gate"]["status"] == "passed"
    assert report["mock_cpu_gate"]["tests_passed"] == 77
    assert set(report["mock_cpu_gate"]["capabilities_proven"]) == {
        "async_delegation",
        "cancel",
        "evaluation",
        "events",
        "export",
        "mcp_contract",
        "modalities",
        "registry",
        "runtime_handoff",
        "split",
        "studio_contract",
        "upload",
        "validation",
    }
    assert report["nvidia_live_smoke"]["status"] == "not_run"
    assert (
        report["nvidia_live_smoke"]["reason_code"]
        == "local_model_not_configured"
    )
    assert report["nvidia_live_smoke"]["evidence_ids"]["complete"] is False
    assert (
        report["nvidia_live_smoke"]["image_attestation"][
            "runtime_image_digest_supplied"
        ]
        is False
    )
    assert report["nvidia_live_proof"] is False
    assert "private-secret-marker" not in json.dumps(report)


def test_mock_gate_isolates_suites_and_reports_native_process_signals() -> None:
    calls = 0

    def runner(command):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            command,
            -11 if calls == 2 else 0,
            stdout="1 passed",
            stderr="native-detail-must-not-leak",
        )

    report = smoke_gate._mock_gate(runner)

    assert report["status"] == "failed"
    assert report["returncode"] == 1
    assert len(report["suites"]) == 5
    assert report["suites"][1]["reason_code"] == "lora_mock_gate_suite_signal:11"
    assert len(report["isolated_reproduce"]) == 5
    assert "native-detail-must-not-leak" not in json.dumps(report)


def test_required_nvidia_turns_missing_hardware_into_failed_gate() -> None:
    report = run_gate(run_mock=False, nvidia_model=None, require_nvidia=True)

    assert report["ok"] is False
    assert report["nvidia_live_smoke"]["status"] == "not_run"
    assert report["nvidia_live_proof"] is False


def test_skipping_mock_cannot_turn_two_not_run_gates_green() -> None:
    report = run_gate(run_mock=False, nvidia_model=None, require_nvidia=False)

    assert report["ok"] is False
    assert report["mock_cpu_gate"]["status"] == "not_run"
    assert report["nvidia_live_smoke"]["status"] == "not_run"
    assert report["nvidia_live_proof"] is False


def test_tree_hash_is_content_and_relative_path_bound(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "config.json").write_text("{}", encoding="utf-8")
    (second / "config.json").write_text("{}", encoding="utf-8")

    assert _tree_sha256(first) == _tree_sha256(second)
    (second / "config.json").write_text('{"changed":true}', encoding="utf-8")
    assert _tree_sha256(first) != _tree_sha256(second)


def test_tree_hash_rejects_symlinks_special_entries_and_empty_trees(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="non-empty"):
        _tree_sha256(empty)

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    (unsafe / "target").write_bytes(b"model")
    (unsafe / "model.safetensors").symlink_to("target")
    with pytest.raises(ValueError, match="symbolic"):
        _tree_sha256(unsafe)

    (unsafe / "model.safetensors").unlink()
    os.mkfifo(unsafe / "model.safetensors")
    with pytest.raises(ValueError, match="special"):
        _tree_sha256(unsafe)


def test_nvidia_probe_rejects_a_symlinked_model_root_before_external_probes(tmp_path: Path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    alias = tmp_path / "model-alias"
    alias.symlink_to(model, target_is_directory=True)

    def unexpected_runner(_command):
        raise AssertionError("unsafe model roots must fail before probing NVIDIA")

    probe, resolved = _nvidia_probe(alias, unexpected_runner)

    assert probe == {"status": "not_run", "reason_code": "local_model_path_not_admitted"}
    assert resolved is None


def test_worker_image_fingerprint_is_reproducible_and_hash_shaped(monkeypatch) -> None:
    monkeypatch.delenv("ANANTA_LORA_WORKER_IMAGE_SHA256", raising=False)
    first = _worker_image_fingerprint()
    second = _worker_image_fingerprint()

    assert first == second
    assert first["kind"] == "reproducible_build_input_digest"
    assert len(first["sha256"]) == 64


def test_worker_image_fingerprint_covers_every_docker_source_input() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    inputs = _worker_image_build_input_paths()
    input_set = set(inputs)

    assert len(inputs) == len(input_set)
    assert {
        ".dockerignore",
        "docker/compose-next/Dockerfile.lora-training-worker",
        "docker/compose-next/requirements.runtime-http.txt",
        "docker/compose-next/requirements.lora-training-cpu.txt",
        "docker/compose-next/requirements.lora-training-nvidia.txt",
        "worker/__init__.py",
        "worker/runtime/__init__.py",
        "worker/runtime/lora_training_app.py",
        "worker/training/__init__.py",
        "worker/training/backends/__init__.py",
    } <= input_set
    assert {
        path.relative_to(repository_root).as_posix()
        for path in (repository_root / "ananta_contracts").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}
    } <= input_set
    assert not any("__pycache__" in Path(path).parts for path in inputs)


def _passed_unsloth_smoke_run(index: int) -> dict[str, object]:
    stages = {
        stage: {"status": "passed"}
        for stage in (
            "training",
            "export",
            "training_evaluation",
            "adapter_evaluation",
            "promotion",
            "runtime_load",
            "rollback",
            "tamper_negative_paths",
        )
    }
    stages["chain_sha256"] = f"{index:x}".rjust(64, "0")
    return {
        "status": "passed",
        "dataset_sha256": "d" * 64,
        "configuration_sha256": "c" * 64,
        "model_snapshot_sha256": "b" * 64,
        "gpu_fingerprint_sha256": "a" * 64,
        "library_fingerprint_sha256": "e" * 64,
        "gpu": [{"name": "RTX", "driver": "test", "memory_mib": 10240}],
        "packages": {"unsloth": "test"},
        "peak_vram": {
            "available": True,
            "max_memory_allocated_bytes": 1024 + index,
            "max_memory_reserved_bytes": 2048 + index,
            "cuda_runtime": "12.4",
        },
        "training_metrics": {"adapter": {"eval_loss": 0.5}, "train": {"train_loss": 0.6}},
        "job_identity": {
            "job_id": "nvidia-live-smoke",
            "attempt_id": "nvidia-live-smoke-attempt-1",
            "fencing_token": 1,
            "correlation_id": "nvidia-live-smoke-correlation",
        },
        "artifacts": {"evaluation.json": {"sha256": "f" * 64, "size_bytes": 128}},
        "runtime_export": {
            "format": "gguf",
            "quantization_method": "q4_k_m",
            "filename": "model.Q4_K_M.gguf",
            "sha256": f"{index:x}".rjust(64, "0"),
            "size_bytes": 1024 + index,
        },
        "platform_stage_coverage": stages,
    }


def test_unsloth_compatibility_profile_is_not_run_when_not_configured() -> None:
    selection = smoke_gate._compatibility_matrix_entry(None, None)

    assert selection == {
        "status": "not_run",
        "reason_code": "compatibility_matrix_entry_not_configured",
    }


def test_unsloth_compatibility_profile_attests_exact_environment() -> None:
    packages = {
        "bitsandbytes": "0.45.5",
        "peft": "0.18.0",
        "safetensors": "0.6.2",
        "torch": "2.6.0+cu124",
        "torchao": "0.13.0",
        "transformers": "4.57.3",
        "trl": "0.24.0",
        "unsloth": "2026.7.5",
        "unsloth-zoo": "2026.7.6",
    }
    selection = {
        "status": "selected",
        "entry_id": "release-profile",
        "matrix_sha256": "a" * 64,
        "entry": {
            "python": "3.11.15",
            "cuda_runtime": "12.4",
            "minimum_nvidia_driver": "550.54.14",
            "approved_model_basenames": ["tiny-causal-lm"],
            "packages": packages,
            "required_deterministic_runs": 3,
        },
    }

    attestation = smoke_gate._compatibility_attestation(
        selection,
        probe={
            "cuda_runtime": "12.4",
            "model_basename": "tiny-causal-lm",
            "gpu": [{"driver": "550.54.14"}],
        },
        versions={"python": "3.11.15", "packages": packages},
        image_attestation={"runtime_image_digest_supplied": True},
        required_runs=3,
        completed_runs=3,
    )

    assert attestation["status"] == "passed"
    assert attestation["profile_id"] == "release-profile"


def test_unsloth_release_requires_three_independent_passed_runs() -> None:
    attestation = {"status": "passed", "entry_id": "release-profile"}

    incomplete = smoke_gate._aggregate_nvidia_runs(
        [_passed_unsloth_smoke_run(1), _passed_unsloth_smoke_run(2)],
        compatibility_attestation=attestation,
    )
    complete = smoke_gate._aggregate_nvidia_runs(
        [_passed_unsloth_smoke_run(1), _passed_unsloth_smoke_run(2), _passed_unsloth_smoke_run(3)],
        compatibility_attestation=attestation,
    )

    assert incomplete["status"] == "not_run"
    assert incomplete["reason_code"] == "deterministic_run_count_incomplete"
    assert complete["status"] == "passed"
    assert complete["deterministic_run_count"] == 3
    assert complete["telemetry_attestation"]["status"] == "passed"
    assert len(complete["runs"]) == 3
    assert all(run["attestation_sha256"] for run in complete["runs"])
    assert complete["runs"][0]["peak_vram"]["max_memory_allocated_bytes"] == 1025
    assert complete["runs"][2]["runtime_export"]["format"] == "gguf"


def test_unsloth_release_rejects_missing_run_telemetry() -> None:
    runs = [_passed_unsloth_smoke_run(index) for index in range(1, 4)]
    runs[1].pop("training_metrics")

    result = smoke_gate._aggregate_nvidia_runs(
        runs,
        compatibility_attestation={"status": "passed"},
    )

    assert result["status"] == "failed"
    assert result["telemetry_attestation"]["status"] == "failed"
    assert "run_2_training_metrics_missing" in result["telemetry_attestation"]["reason_codes"]


def test_unsloth_release_rejects_unbound_job_identity() -> None:
    runs = [_passed_unsloth_smoke_run(index) for index in range(1, 4)]
    runs[0]["job_identity"] = {"job_id": "nvidia-live-smoke"}

    result = smoke_gate._aggregate_nvidia_runs(
        runs,
        compatibility_attestation={"status": "passed"},
    )

    assert result["status"] == "failed"
    assert "run_1_job_identity_invalid" in result["telemetry_attestation"]["reason_codes"]


def test_unsloth_run_attestation_preserves_bounded_reason_code() -> None:
    failed = smoke_gate._aggregate_nvidia_runs(
        [{"status": "failed", "reason_code": "training_dependency_failed"}],
        compatibility_attestation={"status": "passed"},
    )

    assert failed["runs"][0]["reason_code"] == "training_dependency_failed"


def test_unsloth_stage_failure_is_a_failed_run_with_diagnostics() -> None:
    run = _passed_unsloth_smoke_run(1)
    run["platform_stage_coverage"]["adapter_evaluation"] = {
        "status": "failed",
        "reason_code": "adapter_load_failed",
        "diagnostic": "adapter shape mismatch",
    }

    failed = smoke_gate._aggregate_nvidia_runs(
        [run],
        compatibility_attestation={"status": "passed"},
    )

    assert failed["status"] == "failed"
    assert failed["platform_stage_coverage"]["adapter_evaluation"]["run_reason_codes"] == [
        "adapter_load_failed"
    ]
    assert failed["runs"][0]["stage_results"]["adapter_evaluation"] == {
        "status": "failed",
        "reason_code": "adapter_load_failed",
        "diagnostic": "adapter shape mismatch",
    }


def test_worker_diagnostic_is_bounded_and_redacts_transport_and_paths() -> None:
    diagnostic = bounded_worker_diagnostic(
        "Bearer top-secret failed at /models/private/model with https://worker.internal/error "
        + "x" * 600
    )

    assert len(diagnostic) <= 512
    assert "top-secret" not in diagnostic
    assert "/models/private/model" not in diagnostic
    assert "worker.internal" not in diagnostic


def test_every_release_transition_has_a_tamper_denial() -> None:
    transitions = {
        "dataset_to_training": "1" * 64,
        "model_to_training": "2" * 64,
        "adapter_to_export": "3" * 64,
        "export_to_evaluation": "4" * 64,
        "evaluation_to_promotion": "5" * 64,
        "promotion_to_runtime": "6" * 64,
        "runtime_to_rollback": "7" * 64,
    }

    expected_rejections = {
        "dataset_to_training": "dataset_hash_mismatch",
        "model_to_training": "base_model_hash_mismatch",
        "adapter_to_export": "adapter_hash_mismatch",
        "export_to_evaluation": "promotion_execution_hash_mismatch",
        "evaluation_to_promotion": "promotion_provenance_mismatch",
        "promotion_to_runtime": "runtime_handoff_promotion_binding_mismatch",
        "runtime_to_rollback": "runtime_endpoint_revision_conflict",
    }
    result = smoke_gate._transition_tamper_checks(
        transitions,
        observed_rejections=expected_rejections,
    )

    wrong_rejections = dict(expected_rejections)
    wrong_rejections["promotion_to_runtime"] = "wrong_rejection_code"
    wrong_result = smoke_gate._transition_tamper_checks(
        transitions,
        observed_rejections=wrong_rejections,
    )
    missing_rejections = dict(expected_rejections)
    missing_rejections.pop("runtime_to_rollback")
    missing_result = smoke_gate._transition_tamper_checks(
        transitions,
        observed_rejections=missing_rejections,
    )

    assert wrong_result["promotion_to_runtime"]["status"] == "failed"
    assert missing_result["runtime_to_rollback"]["status"] == "not_run"
    assert set(result) == set(transitions)
    assert all(item["status"] == "passed" for item in result.values())
    assert {item["reason_code"] for item in result.values()} == {
        "dataset_hash_mismatch",
        "base_model_hash_mismatch",
        "adapter_hash_mismatch",
        "promotion_execution_hash_mismatch",
        "promotion_provenance_mismatch",
        "runtime_handoff_promotion_binding_mismatch",
        "runtime_endpoint_revision_conflict",
    }
