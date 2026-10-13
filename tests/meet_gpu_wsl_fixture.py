"""Explicit WSL-only driver projection for isolated synthetic GPU probes."""

import re
from pathlib import Path

LIBRARY_ROOT = Path("/usr/lib/wsl/lib")
LIBRARIES = (
    "libcuda.so", "libcuda.so.1", "libnvcuvid.so.1",
    "libnvidia-encode.so.1", "libnvidia-ml.so.1",
    "libnvidia-gpucomp.so", "libdxcore.so", "libd3d12.so", "libd3d12core.so",
)
STORE_FILES = (
    "libcuda.so.1.1", "libcuda_loader.so", "libnvdxgdmal.so.1", "nvcubins.bin",
    "libnvidia-ptxjitcompiler.so.1", "libnvidia-gpucomp.so", "libnvidia-nvvm.so.4",
    "libnvidia-encode.so.1", "libnvcuvid.so.1", "libnvidia-ml.so.1",
)


def validate_driver_store(value):
    """Require explicit selection; never mix libraries from multiple drivers."""
    if not isinstance(value, str) or not re.fullmatch(
        r"/usr/lib/wsl/drivers/nv_dispig\.inf_amd64_[a-f0-9]{16}", value
    ):
        raise ValueError("test_gpu_wsl_driver_store_invalid")
    return Path(value)


def driver_bindings(driver_store, *, is_file=None, is_device=None):
    """Fail before Docker if any fixed local dependency is missing."""
    is_file = is_file or Path.is_file
    is_device = is_device or Path.is_char_device
    store = validate_driver_store(driver_store)
    files = [LIBRARY_ROOT / name for name in LIBRARIES] + [store / name for name in STORE_FILES]
    if not is_device(Path("/dev/dxg")) or any(
        not is_file(path) for path in files
    ):
        raise ValueError("test_gpu_wsl_driver_binding_missing")
    return [
        f"type=bind,src={path},dst={path},readonly" for path in files
    ]
