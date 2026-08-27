from __future__ import annotations

import json
from pathlib import Path

from scripts.run_training_backend_benchmark import decision, validate_result


def _matrix() -> dict:
    return json.loads(Path("benchmarks/training_backends/matrix.v1.json").read_text())


def test_not_run_contract_does_not_claim_metrics() -> None:
    payload = {"backend": "axolotl", "status": "not_run", "metrics": {}}
    assert validate_result(payload, _matrix()) == []
    assert decision(payload, _matrix()) == "no-go"


def test_verified_result_requires_complete_hardware_and_digest_attestation() -> None:
    payload = {
        "backend": "axolotl",
        "status": "verified",
        "bindings": {
            "backend_version": "0.18.0",
            "config_sha256": "a" * 64,
            "container_digest": "sha256:" + "b" * 64,
            "dataset_sha256": "c" * 64,
            "hardware_attestation_sha256": "d" * 64,
            "model_sha256": "e" * 64,
            "seed": 42,
        },
        "metrics": {field: 1.0 for field in _matrix()["required_metrics"]},
        "hardware": {"gpu_model": "NVIDIA GeForce RTX 3080", "vram_bytes": 10 * 1024**3},
    }
    assert validate_result(payload, _matrix()) == []
    assert decision(payload, _matrix()) == "conditional-go"
    payload["bindings"].pop("container_digest")
    assert "bindings.container_digest is invalid" in validate_result(payload, _matrix())


def test_unmaintained_backend_is_always_no_go() -> None:
    payload = {"backend": "autotrain", "status": "not_run", "metrics": {}}
    assert decision(payload, _matrix()) == "no-go"
