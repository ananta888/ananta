from pathlib import Path

import pytest

from scripts.run_hub_evidence_unsloth_gpu_gate import (
    UnslothGpuGateError,
    bounded_diagnostic,
    build_container_command,
    docker_image_revision,
)


def test_bounded_diagnostic_keeps_only_a_printable_tail() -> None:
    diagnostic = bounded_diagnostic("prefix\x00" + "x" * 2100 + "failure")

    assert len(diagnostic) == 2000
    assert diagnostic.endswith("failure")
    assert "\x00" not in diagnostic


def test_container_command_receives_only_hub_assignment_and_immutable_inputs(tmp_path: Path) -> None:
    model = tmp_path / "tiny-causal-lm"
    model.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    nvidia_smi = tmp_path / "nvidia-smi"
    nvidia_smi.write_text("", encoding="utf-8")
    devices = []
    for name in ("nvidia0", "nvidiactl", "nvidia-uvm", "nvidia-uvm-tools"):
        device = tmp_path / name
        device.write_text("", encoding="utf-8")
        devices.append(device)
    libraries = {}
    for name in ("libcuda.so.1", "libnvidia-ml.so.1", "libnvidia-ptxjitcompiler.so.1"):
        path = tmp_path / name
        path.write_text("", encoding="utf-8")
        libraries[name] = path

    command = build_container_command(
        image="worker@sha256:" + "a" * 64,
        image_id="sha256:" + "b" * 64,
        model_path=model,
        output_dir=output,
        assignment={"run_id": "RUN_bound", "source_ids": ["SRC_repo", "SRC_model"]},
        matrix_entry="matrix-entry",
        timeout_seconds=600,
        root=root,
        libraries=libraries,
        device_paths=devices,
        nvidia_smi_path=nvidia_smi,
    )

    assert "ANANTA_UNSLOTH_SRC_IDS=SRC_repo,SRC_model" in command
    assert "ANANTA_UNSLOTH_RUN_IDS=RUN_bound" in command
    assert "ANANTA_LORA_WORKER_IMAGE_SHA256=sha256:" + "b" * 64 in command
    assert "NVIDIA_VISIBLE_DEVICES=0" in command
    assert "PYTHONPATH=/app:/gate" in command
    assert "TRITON_CACHE_DIR=/tmp/triton-cache" in command
    assert "CUDA_CACHE_PATH=/tmp/cuda-cache" in command
    assert "NUMBA_CACHE_DIR=/tmp/numba-cache" in command
    assert "UNSLOTH_COMPILE_LOCATION=/tmp/unsloth-compiled-cache" in command
    assert f"{libraries['libcuda.so.1']}:/host-nvidia/libcuda.so:ro" in command
    assert "/tmp:rw,exec,nosuid,nodev,size=4294967296,uid=10005,gid=10005" in command
    assert "none" in command
    assert f"{model}:/models/tiny-causal-lm:ro" in command
    assert "--repeat" in command and command[command.index("--repeat") + 1] == "3"


def test_container_command_rejects_unreserved_assignment(tmp_path: Path) -> None:
    model = tmp_path / "tiny-causal-lm"
    model.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    nvidia_smi = tmp_path / "nvidia-smi"
    nvidia_smi.write_text("", encoding="utf-8")

    with pytest.raises(UnslothGpuGateError, match="unsloth_gate_assignment_invalid"):
        build_container_command(
            image="worker:latest",
            image_id="sha256:" + "b" * 64,
            model_path=model,
            output_dir=output,
            assignment={"run_id": "caller-run", "source_ids": []},
            matrix_entry="matrix-entry",
            timeout_seconds=600,
            root=root,
            libraries={},
            device_paths=[],
            nvidia_smi_path=nvidia_smi,
        )


def test_image_revision_requires_a_commit_bound_oci_label(monkeypatch: pytest.MonkeyPatch) -> None:
    revision = "a" * 40
    monkeypatch.setattr(
        "scripts.run_hub_evidence_unsloth_gpu_gate.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": revision})(),
    )

    assert docker_image_revision("worker:gate") == revision


def test_image_revision_rejects_an_unbound_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.run_hub_evidence_unsloth_gpu_gate.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "unbound"})(),
    )

    with pytest.raises(UnslothGpuGateError, match="unsloth_gate_worker_image_revision_invalid"):
        docker_image_revision("worker:latest")
