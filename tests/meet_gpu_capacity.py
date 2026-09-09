"""Read-only readiness check for the opt-in GPU gate, not a reservation or grant."""

import re
import subprocess

MINIMUM_FREE_MIB = 4096  # Conservative space for the pinned Qwen/Piper/NVENC pair.


def require_gpu_capacity(*, run=subprocess.run):
    try:
        result = run(
            ["nvidia-smi", "--id=0", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        raw = result.stdout.strip()
        if not re.fullmatch(r"[0-9]{1,7}", raw):
            raise ValueError("test_inference_gpu_capacity_invalid")
        if int(raw) < MINIMUM_FREE_MIB:
            raise ValueError("test_inference_gpu_capacity_unavailable")
        return int(raw)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("test_inference_gpu_capacity_unavailable") from None
