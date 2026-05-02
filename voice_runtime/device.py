from __future__ import annotations

import os
import platform


def detect_runtime_device(preferred: str = "auto") -> dict:
    requested = str(preferred or "auto").strip().lower() or "auto"
    if requested in {"cpu", "cuda", "vulkan", "metal"}:
        return {"requested": requested, "effective": requested, "reason": "explicit"}

    # Lightweight heuristic without hard dependency on GPU libraries.
    if os.getenv("CUDA_VISIBLE_DEVICES") not in {None, "", "-1"}:
        return {"requested": "auto", "effective": "cuda", "reason": "cuda_visible_devices"}
    if platform.system().lower() == "darwin":
        return {"requested": "auto", "effective": "metal", "reason": "darwin_default"}
    return {"requested": "auto", "effective": "cpu", "reason": "safe_default"}
