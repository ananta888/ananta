#!/usr/bin/env python3
"""MDP-020 / MIMG-016: Smoke script for Markdown/Mermaid rendering.

Usage:
  python scripts/operator_tui_markdown_mermaid_visible_smoke.py [--adapter ansi|kitty|sixel] [--require-image]

Prints capability diagnostics and renders a sample Markdown+Mermaid document
through the selected adapter, reporting whether image output or text fallback was used.
"""
from __future__ import annotations

import argparse
import sys
import time

_SAMPLE_MD = """# Markdown + Mermaid Smoke Test

This is a **bold** paragraph with `inline code` and *italic* text.

## Sequence Diagram

```mermaid
sequenceDiagram
    participant User
    participant TUI
    participant AI
    User->>TUI: Ctrl+Space
    TUI->>AI: Frage stellen
    AI-->>TUI: Antwort
    TUI-->>User: Chatnachricht
```

## Flowchart

```mermaid
graph TD
    A[Start] --> B{Renderer verfügbar?}
    B -- ja --> C[Kitty/Sixel-Bild]
    B -- nein --> D[ANSI Fallback]
    C --> E[Ende]
    D --> E
```

## Code Block

```python
def greet(name: str) -> str:
    return f"Hello, {name}!"
```

## Table

| Feature        | Status  |
|----------------|---------|
| Markdown ANSI  | ✓ ready |
| Mermaid image  | depends |
| Kitty output   | depends |
| Sixel output   | depends |
"""


def _print_capabilities() -> dict:
    print("\n=== Capability Diagnostics ===")
    import shutil
    caps: dict = {}

    # Mermaid backends
    mmdc = shutil.which("mmdc")
    caps["mmdc"] = bool(mmdc)
    print(f"  mmdc (mermaid-cli):   {'✓ ' + mmdc if mmdc else '✗ not found in PATH'}")

    try:
        import importlib.util
        pw_ok = importlib.util.find_spec("playwright") is not None
    except Exception:
        pw_ok = False
    caps["playwright"] = pw_ok
    print(f"  playwright:           {'✓ installed' if pw_ok else '✗ not installed'}")

    try:
        import cairosvg  # type: ignore
        cairo_ok = True
    except Exception:
        cairo_ok = False
    caps["cairosvg"] = cairo_ok
    print(f"  cairosvg:             {'✓ available' if cairo_ok else '✗ not installed'}")

    try:
        from PIL import Image  # type: ignore
        pil_ok = True
    except Exception:
        pil_ok = False
    caps["pillow"] = pil_ok
    print(f"  Pillow:               {'✓ available' if pil_ok else '✗ not installed'}")

    # Terminal
    import os
    term = os.environ.get("TERM", "")
    term_prog = os.environ.get("TERM_PROGRAM", "")
    kitty_ok = "kitty" in term.lower() or "kitty" in term_prog.lower()
    sixel_ok = os.environ.get("SIXEL_SUPPORTED", "").lower() in {"1", "true", "yes"}
    caps["kitty"] = kitty_ok
    caps["sixel"] = sixel_ok
    print(f"  Kitty terminal:       {'✓ detected' if kitty_ok else '✗ not detected (TERM=' + term + ')'}")
    print(f"  Sixel terminal:       {'✓ SIXEL_SUPPORTED=1' if sixel_ok else '✗ not detected (set SIXEL_SUPPORTED=1 to enable)'}")
    return caps


def _render_ansi(width: int = 80) -> None:
    from client_surfaces.operator_tui.visual.markdown.markdown_parser import parse_markdown
    from client_surfaces.operator_tui.visual.markdown.markdown_ansi_renderer import render_markdown_ansi_lines
    import re

    print("\n=== ANSI Markdown Rendering ===")
    blocks = parse_markdown(_SAMPLE_MD)
    lines = render_markdown_ansi_lines(blocks, width=width)
    for line in lines[:30]:
        print(line)
    if len(lines) > 30:
        print(f"  ... ({len(lines) - 30} more lines)")


def _render_mermaid_check(caps: dict) -> dict[str, str]:
    from client_surfaces.operator_tui.visual.markdown.mermaid_renderer import MermaidRenderer

    print("\n=== Mermaid Renderer Status ===")
    renderer = MermaidRenderer()
    status = renderer.capability_status()
    results: dict[str, str] = {}
    for name, (ok, reason) in status.items():
        label = "✓ available" if ok else f"✗ {reason}"
        print(f"  {name:<22} {label}")
        results[name] = "available" if ok else reason

    # Try rendering the first diagram
    test_diagram = "graph TD\n  A --> B"
    t0 = time.perf_counter()
    result = renderer.render(test_diagram)
    elapsed = (time.perf_counter() - t0) * 1000.0
    if result.success and result.image_data:
        fmt = result.image_format
        size = len(result.image_data)
        print(f"\n  Sample render: ✓ {fmt.upper()} {size} bytes ({elapsed:.0f}ms)")
        results["sample_render"] = f"ok:{fmt}:{size}"
    else:
        reason = result.reason or "graceful fallback"
        print(f"\n  Sample render: ✗ {reason} ({elapsed:.0f}ms)")
        results["sample_render"] = f"fail:{reason}"
    return results


def _render_kitty(caps: dict) -> None:
    if not caps.get("kitty"):
        print("\n[kitty] Terminal not detected — skipping image output")
        print("        Set TERM=xterm-kitty or use Kitty terminal for image output")
        return

    from client_surfaces.operator_tui.visual.markdown.mermaid_renderer import MermaidRenderer
    from client_surfaces.operator_tui.visual.renderers.cpu_raster_renderer import CpuRasterRenderer
    from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene
    from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext
    from client_surfaces.operator_tui.visual.adapters.kitty_adapter import KittyOutputAdapter
    from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawContext
    from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion
    import sys

    print("\n[kitty] Attempting Mermaid→PNG→Kitty output...")
    renderer = MermaidRenderer()
    result = renderer.render("graph TD\n  A[OK] --> B[Done]")
    if not (result.success and result.image_data):
        print(f"  ✗ Mermaid render failed: {result.reason}")
        return

    node = {
        "kind": "diagram_image",
        "diagram_id": "smoke_1",
        "image_format": result.image_format,
        "image_data": result.image_data,
        "x": 0, "y": 0,
        "requested_width": 400, "requested_height": 200,
        "alt_text": "Smoke test diagram",
    }
    scene = RenderScene(scene_type="markdown_mermaid_document", nodes=[node], metadata={"animated": False})
    cpu = CpuRasterRenderer(max_width=400, max_height=200)
    frame = cpu.render(scene, width=400, height=200, context=RenderContext(now=time.monotonic()))

    if frame.mime_or_format != "image/png":
        print(f"  ✗ No PNG frame (got {frame.mime_or_format})")
        return

    region = ViewportRegion(x=0, y=0, columns=50, rows=12, pixel_width=400, pixel_height=200)
    adapter = KittyOutputAdapter(supported=True, enabled=True)
    draw_result = adapter.draw(frame, region=region, stream=sys.stdout, context=DrawContext(now=time.monotonic()))
    if draw_result.drawn:
        print("\n  ✓ Kitty image sent successfully")
    else:
        print(f"\n  ✗ Kitty draw failed: {draw_result.reason}")


def _render_sixel(caps: dict) -> None:
    from client_surfaces.operator_tui.visual.adapters.sixel_adapter import SixelOutputAdapter
    from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawContext
    from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame
    from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion
    import sys

    print("\n[sixel] Checking Sixel encoder...")
    if not caps.get("sixel"):
        print("  Terminal Sixel not detected (set SIXEL_SUPPORTED=1 to enable)")

    adapter = SixelOutputAdapter(supported=caps.get("sixel", False), enabled=True)
    payload = bytes([200, 100, 50, 255] * (4 * 4))  # tiny 4x4 RGBA image
    frame = RenderFrame(
        frame_type="raster", width=4, height=4,
        payload={"pixels": payload, "width": 4, "height": 4, "mode": "RGBA"},
        mime_or_format="application/x-rgba", timestamp=1.0, metadata={},
    )
    region = ViewportRegion(x=0, y=0, columns=10, rows=4, pixel_width=40, pixel_height=16)
    result = adapter.draw(frame, region=region, stream=sys.stdout, context=DrawContext(now=time.monotonic()))
    if result.drawn:
        print("\n  ✓ Sixel output sent (real encoding)")
    elif "encoder_unavailable" in str(result.reason):
        print(f"  ~ Degraded: {result.reason} (install Pillow for Sixel encoding)")
    else:
        print(f"  ✗ {result.reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Markdown/Mermaid smoke test")
    parser.add_argument("--adapter", choices=["ansi", "kitty", "sixel"], default="ansi")
    parser.add_argument("--require-image", action="store_true", help="Exit non-zero if only fallback was used")
    parser.add_argument("--width", type=int, default=80)
    args = parser.parse_args()

    caps = _print_capabilities()
    mermaid_results = _render_mermaid_check(caps)
    sample_ok = mermaid_results.get("sample_render", "").startswith("ok:")

    if args.adapter == "ansi":
        _render_ansi(width=args.width)
    elif args.adapter == "kitty":
        _render_kitty(caps)
    elif args.adapter == "sixel":
        _render_sixel(caps)

    print("\n=== Summary ===")
    print(f"  Adapter:             {args.adapter}")
    print(f"  Mermaid image:       {'✓ available' if sample_ok else '✗ fallback only'}")
    print(f"  ANSI fallback:       ✓ always available")

    if args.require_image and not sample_ok:
        print("\n✗ --require-image: image rendering not available")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
