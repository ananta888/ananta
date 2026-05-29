from __future__ import annotations

import time
from dataclasses import dataclass

from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame, RenderScene


def _clip_text(text: str, width: int) -> str:
    if width <= 0:
        return ""
    value = str(text or "")
    return value[:width]


@dataclass
class AnsiBlocksRenderer:
    renderer_id: str = "ansi_blocks"

    def render(self, scene: RenderScene, *, width: int, height: int, context: RenderContext) -> RenderFrame:
        start = time.perf_counter()
        cols = max(1, int(width))
        rows = max(1, int(height))
        grid = [" " * cols for _ in range(rows)]

        def set_line(y: int, text: str) -> None:
            if y < 0 or y >= rows:
                return
            line = _clip_text(text, cols).ljust(cols)
            grid[y] = line

        set_line(0, f"[{scene.scene_type}]")
        for node in scene.nodes:
            if not isinstance(node, dict):
                continue
            kind = str(node.get("kind") or "")
            text = str(node.get("text") or "")
            y = int(node.get("y") or 0)
            x = int(node.get("x") or 0)
            if kind in {"label", "title", "placeholder", "error", "logo_block"}:
                if x > 0 and x < cols:
                    prefix = " " * x
                    set_line(y, prefix + text)
                else:
                    set_line(y, text)
            elif kind == "territory":
                tid = str(node.get("id") or "?")
                owner = str(node.get("owner") or "-")
                point = node.get("point")
                py = int(point[1]) if isinstance(point, (list, tuple)) and len(point) == 2 else y
                set_line(py, f"* {tid} [{owner}]")
            elif kind == "snake_head":
                point = node.get("point")
                py = int(point[1]) if isinstance(point, (list, tuple)) and len(point) == 2 else y
                set_line(py, "● snake-head")
            elif kind == "target":
                point = node.get("point")
                py = int(point[1]) if isinstance(point, (list, tuple)) and len(point) == 2 else y
                set_line(py, "◎ target")
            elif kind == "snake_path":
                points = node.get("points") if isinstance(node.get("points"), list) else []
                set_line(y, f"path-points={len(points)}")

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return RenderFrame(
            frame_type="ansi",
            width=cols,
            height=rows,
            payload=grid,
            mime_or_format="text/plain",
            timestamp=context.now,
            metadata={
                "renderer": self.renderer_id,
                "scene_type": scene.scene_type,
                "generation_ms": round(elapsed_ms, 4),
                "animated": bool(scene.metadata.get("animated")),
                # Pass through scroll metadata so renderer.py can draw scrollbars
                **{k: scene.metadata[k] for k in (
                    "content_lines", "max_line_width", "scroll_offset", "h_offset"
                ) if k in scene.metadata},
            },
        )

