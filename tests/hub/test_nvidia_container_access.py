from __future__ import annotations

from pathlib import Path

import pytest

from scripts.nvidia_container_access import (
    LINUX_NATIVE_PROFILE,
    WSL2_PROFILE,
    NvidiaContainerAccessError,
    resolve_nvidia_container_access,
)
from scripts.run_hub_evidence_unsloth_gpu_gate import build_container_command
from scripts.unsloth_ollama_runtime_probe import build_ollama_container_command


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def _wsl_host(tmp_path: Path) -> tuple[Path, Path, Path]:
    dev = tmp_path / "dev"
    wsl = tmp_path / "wsl"
    _touch(dev / "dxg")
    for name in ("libcuda.so.1", "libdxcore.so", "libnvidia-ml.so.1"):
        _touch(wsl / "lib" / name)
    nvidia_smi = _touch(wsl / "lib" / "nvidia-smi")
    _touch(wsl / "drivers" / "nv_dispi.inf_amd64_0123" / "libcuda.so.1.1")
    _touch(wsl / "drivers" / "other_vendor.inf_amd64_4567" / "libvendor.so")
    return dev, wsl, nvidia_smi


def test_wsl2_access_binds_dxg_loader_libraries_and_only_nvidia_driver_store(tmp_path: Path) -> None:
    dev, wsl, nvidia_smi = _wsl_host(tmp_path)

    access = resolve_nvidia_container_access(dev_root=dev, wsl_root=wsl, which=lambda _: str(nvidia_smi))

    assert access.profile == WSL2_PROFILE
    assert access.device_paths == (dev / "dxg",)
    assert set(access.libraries) == {"libcuda.so.1", "libdxcore.so", "libnvidia-ml.so.1"}
    assert access.driver_store_paths == (wsl / "drivers" / "nv_dispi.inf_amd64_0123",)
    assert all(str(path).startswith(str(wsl / "lib")) for path in access.libraries.values())


def test_native_device_nodes_take_precedence_over_wsl(tmp_path: Path) -> None:
    dev, wsl, nvidia_smi = _wsl_host(tmp_path)
    for name in ("nvidia0", "nvidiactl", "nvidia-uvm", "nvidia-uvm-tools"):
        _touch(dev / name)
    libraries = {
        name: _touch(tmp_path / "host-lib" / name)
        for name in ("libcuda.so.1", "libnvidia-ml.so.1", "libnvidia-ptxjitcompiler.so.1")
    }
    ldconfig = "\n".join(f"\t{name} (libc6,x86-64) => {path}" for name, path in libraries.items())

    access = resolve_nvidia_container_access(
        dev_root=dev, wsl_root=wsl, ldconfig_output=lambda: ldconfig, which=lambda _: str(nvidia_smi)
    )

    assert access.profile == LINUX_NATIVE_PROFILE
    assert access.driver_store_paths == ()
    assert access.libraries == libraries


def test_wsl2_access_fails_closed_without_nvidia_driver_store(tmp_path: Path) -> None:
    dev, wsl, nvidia_smi = _wsl_host(tmp_path)
    (wsl / "drivers" / "nv_dispi.inf_amd64_0123" / "libcuda.so.1.1").unlink()

    with pytest.raises(NvidiaContainerAccessError, match="nvidia_driver_store_unavailable"):
        resolve_nvidia_container_access(dev_root=dev, wsl_root=wsl, which=lambda _: str(nvidia_smi))


def test_missing_gpu_device_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(NvidiaContainerAccessError, match="nvidia_device_unavailable"):
        resolve_nvidia_container_access(dev_root=tmp_path, wsl_root=tmp_path, which=lambda _: None)


def test_gate_and_provider_commands_mount_the_driver_store_read_only(tmp_path: Path) -> None:
    dev, wsl, nvidia_smi = _wsl_host(tmp_path)
    access = resolve_nvidia_container_access(dev_root=dev, wsl_root=wsl, which=lambda _: str(nvidia_smi))
    store = access.driver_store_paths[0]
    model = tmp_path / "tiny-causal-lm"
    model.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    state = tmp_path / "state"
    state.mkdir()

    worker = build_container_command(
        image="worker:gate",
        image_id="sha256:" + "b" * 64,
        model_path=model,
        output_dir=output,
        assignment={"run_id": "RUN_bound", "source_ids": ["SRC_repo"]},
        matrix_entry="entry",
        timeout_seconds=600,
        root=tmp_path,
        libraries=access.libraries,
        device_paths=access.device_paths,
        nvidia_smi_path=access.nvidia_smi_path,
        driver_store_paths=access.driver_store_paths,
    )
    provider = build_ollama_container_command(
        image="ollama/ollama@sha256:" + "a" * 64,
        container_name="ananta-unsloth-ollama-0123456789abcdef",
        state_dir=state,
        libraries=access.libraries,
        device_paths=access.device_paths,
        nvidia_smi_path=access.nvidia_smi_path,
        driver_store_paths=access.driver_store_paths,
    )

    for command in (worker, provider):
        assert f"{store}:{store}:ro" in command
        assert command[command.index("--device") + 1] == str(dev / "dxg")
