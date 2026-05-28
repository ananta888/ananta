from __future__ import annotations

import time
from dataclasses import dataclass

from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame, RenderScene


@dataclass
class OpenGlOffscreenRenderer:
    renderer_id: str = "opengl_offscreen_optional"
    max_width: int = 1280
    max_height: int = 720

    def _check_available(self) -> tuple[bool, str]:
        try:
            import moderngl  # type: ignore  # noqa: F401
        except Exception as exc:  # pragma: no cover - depends on runtime env
            return False, f"moderngl unavailable: {exc}"
        return True, "ok"

    def render(self, scene: RenderScene, *, width: int, height: int, context: RenderContext) -> RenderFrame:
        start = time.perf_counter()
        w = max(1, min(int(width), self.max_width))
        h = max(1, min(int(height), self.max_height))
        available, reason = self._check_available()
        if not available:
            raise RuntimeError(reason)

        # Optional plugin path: return a normal raster frame for existing adapters.
        # Real GPU offscreen rendering can replace this placeholder without changing
        # runtime contracts.
        payload = bytes([18, 28, 42, 255]) * (w * h)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return RenderFrame(
            frame_type="raster",
            width=w,
            height=h,
            payload=payload,
            mime_or_format="application/x-rgba",
            timestamp=context.now,
            metadata={
                "renderer": self.renderer_id,
                "scene_type": scene.scene_type,
                "generation_ms": round(elapsed_ms, 4),
                "animated": bool(scene.metadata.get("animated")),
                "opengl_plugin": True,
            },
        )

