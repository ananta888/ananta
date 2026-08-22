from __future__ import annotations

import time

import pytest

from agent.services.hrm_experiments import default_hrm_contract_validator
from worker.hrm_experiments.runner import (
    HrmExperimentRunner,
    HrmRunnerConfiguration,
    HrmRunnerError,
    canonical_digest,
    hrm_contract_schema_digest,
    run_payload_digest,
)


def _runtime() -> dict:
    return {
        "engine_version": "hrm-runner-v1",
        "image_digest": "a" * 64,
        "python_version": "3.11.15",
        "torch_version": None,
        "cuda_version": None,
        "flash_attention_version": None,
    }


def _runner() -> HrmExperimentRunner:
    isolation = {
        "profile_version": "hrm-container-v1",
        "non_root": True,
        "no_new_privileges": True,
        "cap_drop_all": True,
        "read_only_rootfs": True,
        "network_denied": True,
        "cgroup_limits": True,
        "seccomp": True,
        "mac_policy": True,
    }
    return HrmExperimentRunner(
        HrmRunnerConfiguration(
            worker_id="worker-hrm-1",
            runtime=_runtime(),
            device={"kind": "cpu", "device_ids": [], "vram_bytes": 0},
            isolation=isolation,
            max_limits={
                "cpu_millis": 4000,
                "memory_bytes": 4_294_967_296,
                "pids": 128,
                "wallclock_seconds": 3600,
                "scratch_bytes": 4_294_967_296,
                "output_bytes": 268_435_456,
                "log_bytes": 33_554_432,
                "event_count": 100_000,
                "retries": 0,
                "gpu_device_ids": [],
                "vram_bytes": 0,
            },
        )
    )


def _envelope() -> dict:
    records = [
        {
            "puzzle_id": "sudoku-1",
            "puzzle": [
                [0, 2, 3, 4, 5, 6, 7, 8, 9],
                [4, 5, 6, 7, 8, 9, 1, 2, 3],
                [7, 8, 9, 1, 2, 3, 4, 5, 6],
                [2, 3, 4, 5, 6, 7, 8, 9, 1],
                [5, 6, 7, 8, 9, 1, 2, 3, 4],
                [8, 9, 1, 2, 3, 4, 5, 6, 7],
                [3, 4, 5, 6, 7, 8, 9, 1, 2],
                [6, 7, 8, 9, 1, 2, 3, 4, 5],
                [9, 1, 2, 3, 4, 5, 6, 7, 8],
            ],
            "solution": [
                [1, 2, 3, 4, 5, 6, 7, 8, 9],
                [4, 5, 6, 7, 8, 9, 1, 2, 3],
                [7, 8, 9, 1, 2, 3, 4, 5, 6],
                [2, 3, 4, 5, 6, 7, 8, 9, 1],
                [5, 6, 7, 8, 9, 1, 2, 3, 4],
                [8, 9, 1, 2, 3, 4, 5, 6, 7],
                [3, 4, 5, 6, 7, 8, 9, 1, 2],
                [6, 7, 8, 9, 1, 2, 3, 4, 5],
                [9, 1, 2, 3, 4, 5, 6, 7, 8],
            ],
        }
    ]
    dataset_digest = canonical_digest({"records": records})
    authority = {
        "task_id": "task-1",
        "assignment_id": "assignment-1",
        "worker_job_id": "worker-job-1",
        "dispatch_lease_id": "lease-1",
        "attempt_id": "attempt-1",
        "epoch": 1,
        "deadline_epoch_ms": time.time_ns() // 1_000_000 + 60_000,
        "policy_digest": "b" * 64,
        "schema_digest": hrm_contract_schema_digest(),
        "payload_digest": "0" * 64,
    }
    run_request = {
        "schema": "ananta.hrm-experiments.run-request.v1",
        "run_id": "run-1",
        "scope": {"tenant_id": "tenant-a", "project_id": "project-a"},
        "authority": authority,
        "profile_id": "hrm-mock-v1",
        "mode": "mock",
        "runtime": _runtime(),
        "dataset_id": "dataset-1",
        "dataset_digest": dataset_digest,
        "checkpoint_id": None,
        "checkpoint_digest": None,
        "limits": {
            "cpu_millis": 1000,
            "memory_bytes": 536_870_912,
            "pids": 64,
            "wallclock_seconds": 300,
            "scratch_bytes": 1_073_741_824,
            "output_bytes": 67_108_864,
            "log_bytes": 8_388_608,
            "event_count": 10_000,
            "retries": 0,
            "gpu_device_ids": [],
            "vram_bytes": 0,
        },
        "seed": 7,
        "precision": "float32",
        "parameters": {"reject_non_finite": True},
    }
    run_request["authority"]["payload_digest"] = run_payload_digest(run_request)
    expected = {
        key: authority[key]
        for key in (
            "task_id",
            "assignment_id",
            "worker_job_id",
            "dispatch_lease_id",
            "attempt_id",
            "epoch",
            "policy_digest",
            "schema_digest",
        )
    }
    admission_unsigned = {
        "dataset_id": "dataset-1",
        "dataset_digest": dataset_digest,
        "checkpoint_id": None,
        "checkpoint_digest": None,
    }
    admission = {
        **admission_unsigned,
        "admission_digest": canonical_digest(admission_unsigned),
    }
    dataset_manifest = {
        "schema": "ananta.hrm-experiments.puzzle-dataset.v1",
        "dataset_id": "dataset-1",
        "scope": {"tenant_id": "tenant-a", "project_id": "project-a"},
        "puzzle_type": "sudoku",
        "source": {
            "kind": "fixture",
            "locator": "fixture:sudoku-1",
            "version": "v1",
            "digest": "e" * 64,
            "license_spdx": "MIT",
        },
        "split": "contract-smoke",
        "record_count": 1,
        "dimensions": {"max_rows": 9, "max_columns": 9, "max_elements": 81},
        "canonical_content_digest": dataset_digest,
        "generator_parameters": {"seed": 7, "augmentation_count": 0},
        "codec_version": "sudoku-codec-v1",
        "normalizer_version": "sudoku-normalizer-v1",
        "validator_version": "sudoku-validator-v1",
        "plugin": {
            "plugin_id": "sudoku",
            "version": "v1",
            "digest": "f" * 64,
            "signature_verified": True,
        },
        "provenance": {
            "imported_at": "2026-08-22T12:00:00Z",
            "importer_version": "hrm-importer-v1",
            "policy_digest": "b" * 64,
        },
    }
    return {
        "run_request": run_request,
        "expected_authority": expected,
        "admission": admission,
        "dataset": {"manifest": dataset_manifest, "records": records},
    }


def test_networkless_runner_executes_only_bound_mock_request():
    runner = _runner()

    result = runner.execute(_envelope())

    assert result["status"] == "completed"
    assert result["run_id"] == "run-1"
    default_hrm_contract_validator.validate("run_result", result)


def test_networkless_runner_rejects_payload_tampering():
    runner = _runner()
    envelope = _envelope()
    envelope["run_request"]["seed"] = 8

    with pytest.raises(HrmRunnerError, match="hrm.payload_digest_mismatch"):
        runner.execute(envelope)


def test_networkless_runner_rejects_unbound_admission():
    runner = _runner()
    envelope = _envelope()
    envelope["admission"]["dataset_digest"] = "d" * 64

    with pytest.raises(HrmRunnerError, match="hrm.admission_digest_mismatch"):
        runner.execute(envelope)
