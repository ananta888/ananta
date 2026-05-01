from __future__ import annotations

DEFAULT_TIMEOUT_SECONDS = 30
MAX_RENDER_SAMPLES = 64


def build_preview_plan(*, width: int = 512, height: int = 512, samples: int = 16, camera: str | None = None) -> dict:
    return {
        "kind": "preview_render",
        "width": max(64, min(int(width), 4096)),
        "height": max(64, min(int(height), 4096)),
        "samples": max(1, min(int(samples), MAX_RENDER_SAMPLES)),
        "camera": str(camera or "").strip() or None,
        "bounded": True,
        "approval_required": False,
    }


def build_final_render_plan(*, output_path: str, width: int = 1920, height: int = 1080, samples: int = 64) -> dict:
    return {
        "kind": "final_render",
        "output_path": str(output_path or "").strip(),
        "width": max(64, min(int(width), 8192)),
        "height": max(64, min(int(height), 8192)),
        "samples": max(1, min(int(samples), MAX_RENDER_SAMPLES)),
        "bounded": True,
        "approval_required": True,
        "execution_mode": "plan_only",
    }
