from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.run_lora_training_smoke import (
    _nvidia_probe,
    _tree_sha256,
    _worker_image_build_input_paths,
    _worker_image_fingerprint,
    run_gate,
)


def test_gate_records_mock_success_and_never_copies_process_output() -> None:
    def runner(command):
        return subprocess.CompletedProcess(command, 0, stdout="77 passed\nprivate-secret-marker", stderr="")

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
        "registry",
        "split",
        "upload",
        "validation",
    }
    assert report["nvidia_live_smoke"] == {
        "status": "not_run",
        "reason_code": "local_model_not_configured",
    }
    assert report["nvidia_live_proof"] is False
    assert "private-secret-marker" not in json.dumps(report)


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
