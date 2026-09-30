"""Least-privilege NVIDIA access for gate containers on native Linux and WSL2 hosts.

The GPU gates start their worker and provider containers without the NVIDIA
container runtime: they pass explicit device nodes and bind only the driver
libraries a CUDA process needs. The concrete host layout differs by platform:

* ``linux-native``: ``/dev/nvidia*`` device nodes and the driver libraries
  registered with ``ldconfig``;
* ``wsl2``: the paravirtualized ``/dev/dxg`` device, the WSL loader libraries
  from ``/usr/lib/wsl/lib`` and the read-only NVIDIA driver store below
  ``/usr/lib/wsl/drivers``. The WSL ``libcuda`` loader resolves the driver
  store by its absolute host path, so it is mounted read-only at that path.

Callers receive one immutable :class:`NvidiaContainerAccess` value and render
it into their own ``docker run`` arguments; they never probe the host
themselves.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

LINUX_NATIVE_PROFILE = "linux-native"
WSL2_PROFILE = "wsl2"

_LINUX_DEVICES = ("nvidia0", "nvidiactl", "nvidia-uvm", "nvidia-uvm-tools")
_LINUX_LIBRARIES = ("libcuda.so.1", "libnvidia-ml.so.1", "libnvidia-ptxjitcompiler.so.1")
_WSL_LIBRARIES = ("libcuda.so.1", "libdxcore.so", "libnvidia-ml.so.1")
_WSL_DRIVER_MARKER = "libcuda.so.1.1"


class NvidiaContainerAccessError(RuntimeError):
    """The host exposes no complete, supported NVIDIA container access profile."""


@dataclass(frozen=True)
class NvidiaContainerAccess:
    profile: str
    device_paths: tuple[Path, ...]
    libraries: Mapping[str, Path]
    nvidia_smi_path: Path
    driver_store_paths: tuple[Path, ...] = field(default=())


def _ldconfig_output() -> str:
    completed = subprocess.run(("ldconfig", "-p"), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise NvidiaContainerAccessError("nvidia_libraries_unavailable")
    return completed.stdout


def _resolve_ldconfig_libraries(output: str) -> dict[str, Path]:
    resolved: dict[str, Path] = {}
    for name in _LINUX_LIBRARIES:
        for line in output.splitlines():
            if line.strip().startswith(f"{name} ") and "=>" in line:
                resolved[name] = Path(line.rsplit("=>", 1)[1].strip()).resolve(strict=True)
                break
    if set(resolved) != set(_LINUX_LIBRARIES):
        raise NvidiaContainerAccessError("nvidia_libraries_unavailable")
    return resolved


def _nvidia_smi(which: Callable[[str], str | None]) -> Path:
    executable = str(which("nvidia-smi") or "").strip()
    if not executable:
        raise NvidiaContainerAccessError("nvidia_device_unavailable")
    return Path(executable).resolve(strict=True)


def resolve_linux_native_access(
    *,
    dev_root: Path = Path("/dev"),
    ldconfig_output: Callable[[], str] = _ldconfig_output,
    which: Callable[[str], str | None] = shutil.which,
) -> NvidiaContainerAccess:
    devices = tuple(dev_root / name for name in _LINUX_DEVICES)
    if not all(device.exists() for device in devices):
        raise NvidiaContainerAccessError("nvidia_device_unavailable")
    return NvidiaContainerAccess(
        profile=LINUX_NATIVE_PROFILE,
        device_paths=devices,
        libraries=_resolve_ldconfig_libraries(ldconfig_output()),
        nvidia_smi_path=_nvidia_smi(which),
    )


def resolve_wsl2_access(
    *,
    dev_root: Path = Path("/dev"),
    wsl_root: Path = Path("/usr/lib/wsl"),
    which: Callable[[str], str | None] = shutil.which,
) -> NvidiaContainerAccess:
    device = dev_root / "dxg"
    library_root = wsl_root / "lib"
    if not device.exists():
        raise NvidiaContainerAccessError("nvidia_device_unavailable")
    libraries = {name: library_root / name for name in _WSL_LIBRARIES}
    if not all(path.is_file() for path in libraries.values()):
        raise NvidiaContainerAccessError("nvidia_libraries_unavailable")
    stores = tuple(
        sorted(
            marker.parent
            for marker in (wsl_root / "drivers").glob(f"*/{_WSL_DRIVER_MARKER}")
            if marker.is_file() and not marker.parent.is_symlink()
        )
    )
    if not stores:
        raise NvidiaContainerAccessError("nvidia_driver_store_unavailable")
    return NvidiaContainerAccess(
        profile=WSL2_PROFILE,
        device_paths=(device,),
        libraries={name: path.resolve(strict=True) for name, path in libraries.items()},
        nvidia_smi_path=_nvidia_smi(which),
        driver_store_paths=stores,
    )


def resolve_nvidia_container_access(
    *,
    dev_root: Path = Path("/dev"),
    wsl_root: Path = Path("/usr/lib/wsl"),
    ldconfig_output: Callable[[], str] = _ldconfig_output,
    which: Callable[[str], str | None] = shutil.which,
) -> NvidiaContainerAccess:
    """Prefer native device nodes; fall back to WSL2 GPU paravirtualization."""
    if (dev_root / "nvidiactl").exists():
        return resolve_linux_native_access(dev_root=dev_root, ldconfig_output=ldconfig_output, which=which)
    if (dev_root / "dxg").exists():
        return resolve_wsl2_access(dev_root=dev_root, wsl_root=wsl_root, which=which)
    raise NvidiaContainerAccessError("nvidia_device_unavailable")


__all__ = [
    "LINUX_NATIVE_PROFILE",
    "WSL2_PROFILE",
    "NvidiaContainerAccess",
    "NvidiaContainerAccessError",
    "resolve_linux_native_access",
    "resolve_nvidia_container_access",
    "resolve_wsl2_access",
]
