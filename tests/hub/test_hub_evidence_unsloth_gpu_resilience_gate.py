from pathlib import Path

import pytest

from scripts.run_hub_evidence_unsloth_gpu_resilience_gate import (
    UnslothGpuResilienceGateError,
    container_command,
)


def test_resilience_container_is_gpu_bound_offline_and_hardened(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    model = tmp_path / "model"
    output = tmp_path / "output"
    for directory in (root, model, output):
        directory.mkdir()
    devices = tuple(tmp_path / name for name in ("gpu", "ctl", "uvm", "uvm-tools"))
    for device in devices:
        device.touch()
    nvidia_smi = tmp_path / "nvidia-smi"
    nvidia_smi.touch()
    library = tmp_path / "libcuda.so.1"
    library.touch()

    command = container_command(
        name="ananta-unsloth-resilience-0123456789abcdef-source",
        image_id="sha256:" + "a" * 64,
        model_path=model,
        output_dir=output,
        phase="source",
        timeout_seconds=300,
        root=root,
        libraries={"libcuda.so.1": library},
        device_paths=devices,
        nvidia_smi_path=nvidia_smi,
    )

    assert command[:4] == [
        "docker",
        "run",
        "--name",
        "ananta-unsloth-resilience-0123456789abcdef-source",
    ]
    assert ["--network", "none"] == command[command.index("--network") : command.index("--network") + 2]
    assert "no-new-privileges:true" in command
    assert "HF_HUB_OFFLINE=1" in command
    assert "TRANSFORMERS_OFFLINE=1" in command
    assert str(root.resolve()) + ":/gate:ro" in command
    assert command[-10:] == [
        "python",
        "/gate/scripts/lora_training_resilience_live.py",
        "--phase",
        "source",
        "--root",
        "/output",
        "--model",
        "/models/model",
        "--timeout-seconds",
        "300",
    ]


def test_resilience_container_name_is_closed(tmp_path: Path) -> None:
    with pytest.raises(UnslothGpuResilienceGateError, match="container_name_invalid"):
        container_command(
            name="unsafe-name",
            image_id="sha256:" + "a" * 64,
            model_path=tmp_path,
            output_dir=tmp_path,
            phase="source",
            timeout_seconds=300,
            root=tmp_path,
            libraries={},
            device_paths=(),
            nvidia_smi_path=tmp_path,
        )
