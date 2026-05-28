#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from client_surfaces.operator_tui.visual.adapters.ansi_adapter import AnsiOutputAdapter
from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawContext
from client_surfaces.operator_tui.visual.adapters.kitty_adapter import KittyOutputAdapter
from client_surfaces.operator_tui.visual.adapters.sixel_adapter import SixelOutputAdapter
from client_surfaces.operator_tui.visual.capabilities.models import TerminalVisualCapabilities
from client_surfaces.operator_tui.visual.renderers.ansi_renderer import AnsiBlocksRenderer
from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext
from client_surfaces.operator_tui.visual.renderers.cpu_raster_renderer import CpuRasterRenderer
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion


def detect_visual_capabilities(env: dict[str, str] | None = None) -> TerminalVisualCapabilities:
    values = env or os.environ
    term = str(values.get("TERM", "")).lower()
    kitty = bool(str(values.get("KITTY_WINDOW_ID") or "").strip()) or term == "xterm-kitty"
    sixel = "sixel" in term
    opengl = str(values.get("ANANTA_TUI_VISUAL_OPENGL", "0")).strip().lower() in {"1", "true", "yes", "on"}
    return TerminalVisualCapabilities(ansi=True, sixel=sixel, kitty_graphics=kitty, opengl_offscreen=opengl)


def preferred_adapter(capabilities: TerminalVisualCapabilities) -> str:
    if capabilities.kitty_graphics:
        return "kitty"
    if capabilities.sixel:
        return "sixel"
    return "ansi"


def _build_scene() -> RenderScene:
    return RenderScene(
        scene_type="visual_smoke",
        nodes=[
            {"kind": "title", "text": "Operator TUI Visual Smoke", "x": 0, "y": 0},
            {"kind": "label", "text": "ANSI/Sixel/Kitty probe", "x": 0, "y": 1},
        ],
        metadata={"animated": False},
    )


def run_smoke(*, adapter_name: str, width: int, height: int, fps: int) -> tuple[int, str]:
    capabilities = detect_visual_capabilities()
    chosen = preferred_adapter(capabilities) if adapter_name == "auto" else adapter_name
    region = ViewportRegion(
        x=0,
        y=0,
        columns=max(8, int(width)),
        rows=max(3, int(height)),
        pixel_width=max(80, int(width) * 10),
        pixel_height=max(48, int(height) * 16),
    )

    if chosen == "ansi":
        renderer = AnsiBlocksRenderer()
        frame = renderer.render(_build_scene(), width=region.columns, height=region.rows, context=RenderContext(now=time.monotonic()))
        adapter = AnsiOutputAdapter()
    else:
        renderer = CpuRasterRenderer()
        frame = renderer.render(_build_scene(), width=region.pixel_width, height=region.pixel_height, context=RenderContext(now=time.monotonic()))
        if chosen == "sixel":
            adapter = SixelOutputAdapter(enabled=True, supported=capabilities.sixel)
        elif chosen == "kitty":
            adapter = KittyOutputAdapter(enabled=True, supported=capabilities.kitty_graphics)
        else:
            return 2, f"unknown adapter: {chosen}"

    now = time.monotonic()
    draw = adapter.draw(frame, region=region, stream=sys.stdout, context=DrawContext(now=now, metadata={"fps": fps}))
    if not draw.drawn:
        fallback = preferred_adapter(capabilities)
        return 2, f"adapter '{chosen}' unsupported ({draw.reason}); try --adapter {fallback}"
    return 0, f"visual-smoke-ok adapter={chosen} fps={fps}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manual smoke-test for Operator TUI visual adapters.")
    parser.add_argument("--adapter", choices=["auto", "ansi", "sixel", "kitty"], default="auto")
    parser.add_argument("--width", type=int, default=64)
    parser.add_argument("--height", type=int, default=12)
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--capabilities-only", action="store_true")
    args = parser.parse_args(argv)

    capabilities = detect_visual_capabilities()
    payload = {
        "ansi": capabilities.ansi,
        "sixel": capabilities.sixel,
        "kitty_graphics": capabilities.kitty_graphics,
        "opengl_offscreen": capabilities.opengl_offscreen,
        "preferred_adapter": preferred_adapter(capabilities),
    }
    print(json.dumps(payload, ensure_ascii=True))
    if args.capabilities_only:
        return 0

    code, message = run_smoke(
        adapter_name=str(args.adapter),
        width=max(8, int(args.width)),
        height=max(3, int(args.height)),
        fps=max(1, int(args.fps)),
    )
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
