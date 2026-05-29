#!/usr/bin/env python3
"""TGFX-018 / TGFX-014: Terminal graphics smoke script for WSL2/Windows environments.

Tests ANSI, Kitty, and Sixel output paths and Mermaid rendering capability.

Usage:
  python scripts/operator_tui_terminal_graphics_smoke.py
  python scripts/operator_tui_terminal_graphics_smoke.py --adapter ansi
  python scripts/operator_tui_terminal_graphics_smoke.py --adapter kitty --force-kitty
  python scripts/operator_tui_terminal_graphics_smoke.py --adapter sixel --force-sixel
  python scripts/operator_tui_terminal_graphics_smoke.py --require-image
  python scripts/operator_tui_terminal_graphics_smoke.py --save-svg /tmp/test.svg
  python scripts/operator_tui_terminal_graphics_smoke.py --open-external  # wslview/xdg-open
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path

_SAMPLE_MERMAID = """flowchart TD
    A[Terminal Graphics Smoke Test]
    B{Kitty/Sixel verfügbar?}
    C[Raster PNG via Kitty]
    D[Raster PNG via Sixel]
    E[ANSI Unicode-Block-Art]
    F[ANSI Quellcode-Fallback]

    A --> B
    B -- Kitty --> C
    B -- Sixel --> D
    B -- ANSI --> E
    C --> G[Ausgabe sichtbar ✓]
    D --> G
    E --> G
    E --> F"""

_SAMPLE_MD = f"""# Terminal Graphics Smoke Test

Dieses Dokument enthält ein Mermaid-Diagramm.

```mermaid
{_SAMPLE_MERMAID}
```

**Bold**, *italic*, `inline code`.

| Adapter | Status |
|---------|--------|
| Kitty | depends |
| Sixel | depends |
| ANSI  | always  |
"""


def _print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def _print_capabilities() -> dict:
    _print_header("Capability Detection")
    from client_surfaces.operator_tui.visual.capabilities.terminal_detector import detect_image_output_capabilities
    caps = detect_image_output_capabilities()
    d = caps.as_dict()
    ok = lambda v: "✓" if v else "✗"

    print(f"  mmdc (mermaid-cli):     {ok(d['mermaid_mmdc'])}")
    print(f"  playwright:             {ok(d['mermaid_playwright'])}")
    print(f"  Pillow:                 {ok(d['pillow_available'])}")
    print(f"  cairosvg:               {ok(d['cairosvg_available'])}")
    print(f"  Kitty protocol:         {ok(d['kitty_supported'])}  (TERM={os.environ.get('TERM','?')}, KITTY_WINDOW_ID={os.environ.get('KITTY_WINDOW_ID','not set')})")
    print(f"  Sixel protocol:         {ok(d['sixel_supported'])}  (SIXEL_SUPPORTED={os.environ.get('SIXEL_SUPPORTED','not set')})")
    print(f"  WT_SESSION (Windows):   {bool(os.environ.get('WT_SESSION'))}")
    print(f"\n  → can_show_mermaid_image: {ok(d['can_show_mermaid_image'])}")
    if d.get("degraded_reasons"):
        print("\n  Degraded reasons:")
        for r in d["degraded_reasons"]:
            print(f"    · {r}")
    return d


def _render_ansi(width: int = 72) -> None:
    _print_header("ANSI/Unicode Rendering")
    from client_surfaces.operator_tui.visual.markdown.markdown_parser import parse_markdown
    from client_surfaces.operator_tui.visual.markdown.mermaid_renderer import MermaidRenderer
    from client_surfaces.operator_tui.visual.markdown.mermaid_block_extractor import extract_mermaid_blocks
    from client_surfaces.operator_tui.visual.markdown.markdown_ansi_renderer import render_markdown_ansi_lines
    from client_surfaces.operator_tui.visual.markdown.block_art_renderer import svg_to_block_art

    blocks = parse_markdown(_SAMPLE_MD)
    renderer = MermaidRenderer()
    diagram_images: dict = {}
    for mb in extract_mermaid_blocks(blocks):
        result = renderer.render(mb.source)
        if result.success and result.image_data:
            diagram_images[mb.source] = (result.image_format or "svg", result.image_data)
            art = svg_to_block_art(result.image_data, max_cols=width, max_rows=16)
            print(f"  Mermaid → block art: {len(art)} lines, {len(result.image_data)} SVG bytes")
        else:
            print(f"  Mermaid failed: {result.reason}")

    lines = render_markdown_ansi_lines(blocks, width=width, diagram_images=diagram_images)
    print()
    for line in lines[:30]:
        print(line)
    if len(lines) > 30:
        print(f"  ... ({len(lines) - 30} more lines)")


def _render_kitty(caps: dict, force: bool = False) -> bool:
    _print_header("Kitty Graphics Output")
    from client_surfaces.operator_tui.visual.markdown.mermaid_renderer import MermaidRenderer
    from client_surfaces.operator_tui.visual.renderers.cpu_raster_renderer import CpuRasterRenderer
    from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext
    from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene
    from client_surfaces.operator_tui.visual.adapters.kitty_adapter import KittyOutputAdapter
    from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawContext
    from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion

    if not caps.get("kitty_supported") and not force:
        print("  ✗ Kitty not detected. Use ANANTA_FORCE_KITTY=1 or --force-kitty to override.")
        return False

    renderer = MermaidRenderer()
    result = renderer.render(_SAMPLE_MERMAID)
    if not result.success:
        print(f"  ✗ Mermaid render failed: {result.reason}")
        return False

    node = {"kind": "diagram_image", "diagram_id": "smoke_kitty", "image_format": result.image_format,
            "image_data": result.image_data, "x": 0, "y": 0, "requested_width": 400, "requested_height": 200,
            "alt_text": "Smoke test diagram"}
    scene = RenderScene(scene_type="markdown_mermaid_document", nodes=[node], metadata={"animated": False})
    cpu = CpuRasterRenderer(max_width=400, max_height=200)
    frame = cpu.render(scene, width=400, height=200, context=RenderContext(now=time.monotonic()))
    if frame.mime_or_format != "image/png":
        print(f"  ✗ No PNG frame: {frame.mime_or_format}")
        return False

    region = ViewportRegion(x=0, y=0, columns=50, rows=12, pixel_width=400, pixel_height=200)
    adapter = KittyOutputAdapter(supported=True, enabled=True)
    draw = adapter.draw(frame, region=region, stream=sys.stdout, context=DrawContext(now=time.monotonic()))
    if draw.drawn:
        print(f"\n  ✓ Kitty image sent ({len(frame.payload or b'')} bytes PNG)")
        return True
    else:
        print(f"  ✗ Kitty draw failed: {draw.reason}")
        return False


def _render_sixel(caps: dict, force: bool = False) -> bool:
    _print_header("Sixel Output")
    from client_surfaces.operator_tui.visual.adapters.sixel_adapter import SixelOutputAdapter
    from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawContext
    from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame
    from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion

    if not caps.get("sixel_supported") and not force:
        print("  ✗ Sixel not detected. Use SIXEL_SUPPORTED=1 or ANANTA_FORCE_SIXEL=1 to override.")
        return False

    # Build a tiny RGBA test frame
    payload = bytes([200, 100, 50, 255] * (8 * 8))
    frame = RenderFrame(
        frame_type="raster", width=8, height=8,
        payload={"pixels": payload, "width": 8, "height": 8, "mode": "RGBA"},
        mime_or_format="application/x-rgba", timestamp=1.0, metadata={},
    )
    region = ViewportRegion(x=0, y=0, columns=10, rows=4, pixel_width=40, pixel_height=16)
    adapter = SixelOutputAdapter(supported=True, enabled=True)
    draw = adapter.draw(frame, region=region, stream=sys.stdout, context=DrawContext(now=time.monotonic()))
    if draw.drawn:
        print(f"\n  ✓ Sixel output sent (real encoding)")
        assert "sixel-stub" not in str(draw.metadata), "Sixel must not emit stub!"
        return True
    else:
        print(f"  ~ Sixel degraded: {draw.reason}")
        print("    (install Pillow for Sixel encoding: pip install Pillow)")
        return False


def _artifacts_dir() -> Path:
    """Return (and create) the default artifacts directory for debug output."""
    d = Path.home() / ".ananta" / "artifacts" / "terminal_graphics_smoke"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_artifact(path: str | None, fmt: str = "svg") -> Path | None:
    """TGFX-014: Save rendered Mermaid SVG or PNG to disk.

    This is a DEBUG/MANUAL path only — not used during normal TUI rendering.
    """
    _print_header(f"DEBUG: Save Mermaid {fmt.upper()} Artifact")
    print("  ⚠  This is a debug/manual path. The TUI always uses the ANSI fallback")
    print("     for rendering — this output is only for external inspection.")
    print()

    from client_surfaces.operator_tui.visual.markdown.mermaid_renderer import MermaidRenderer
    renderer = MermaidRenderer()
    result = renderer.render(_SAMPLE_MERMAID)
    if not result.success or not result.image_data:
        print(f"  ✗ Mermaid render failed: {result.reason}")
        return None

    if path:
        out = Path(path)
    else:
        ts = time.strftime("%Y%m%d_%H%M%S")
        out = _artifacts_dir() / f"mermaid_smoke_{ts}.{fmt}"

    if fmt == "png":
        # Convert SVG → PNG via cairosvg
        try:
            import cairosvg  # type: ignore
            png_bytes = cairosvg.svg2png(bytestring=result.image_data)
            out = out.with_suffix(".png")
            out.write_bytes(png_bytes)
            print(f"  ✓ PNG saved to: {out.resolve()}")
            print(f"     Size: {len(png_bytes)} bytes")
            return out
        except Exception as e:
            print(f"  ✗ PNG conversion failed: {e}")
            print("    (install cairosvg: pip install cairosvg)")
            return None
    else:
        out = out.with_suffix(".svg")
        out.write_bytes(result.image_data)
        print(f"  ✓ SVG saved to: {out.resolve()}")
        print(f"     Size: {len(result.image_data)} bytes")
        return out


def _open_external(artifact_path: Path) -> None:
    """TGFX-014: Open artifact externally via wslview or xdg-open (debug/manual only).

    The opener binary must come from PATH, never from document content.
    """
    import shutil
    opener = shutil.which("wslview") or shutil.which("xdg-open") or shutil.which("open")
    if not opener:
        print("  ✗ No external opener found (install wslview for WSL2, or xdg-open on Linux)")
        return
    print(f"  Opening {artifact_path} with {opener} ...")
    try:
        subprocess.run([opener, str(artifact_path)], timeout=10, check=False)
        print("  ✓ Opener launched")
    except subprocess.TimeoutExpired:
        print("  ~ Opener timed out (file may still open)")
    except Exception as e:
        print(f"  ✗ Opener failed: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Terminal graphics smoke test (TGFX-018/TGFX-014)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test all adapters
  python scripts/operator_tui_terminal_graphics_smoke.py

  # Force Kitty graphics output
  python scripts/operator_tui_terminal_graphics_smoke.py --adapter kitty --force-kitty

  # Save SVG to default artifacts dir (~/.ananta/artifacts/terminal_graphics_smoke/)
  python scripts/operator_tui_terminal_graphics_smoke.py --save-artifact svg

  # Save PNG and open externally (DEBUG/MANUAL path)
  python scripts/operator_tui_terminal_graphics_smoke.py --save-artifact png --open-external

  # Save to specific path
  python scripts/operator_tui_terminal_graphics_smoke.py --save-artifact svg --save-path /tmp/test.svg

  # Fail if only ANSI fallback possible
  python scripts/operator_tui_terminal_graphics_smoke.py --require-image
        """,
    )
    parser.add_argument("--adapter", choices=["ansi", "kitty", "sixel", "all"], default="all")
    parser.add_argument("--force-kitty", action="store_true", help="Force Kitty output even if not detected")
    parser.add_argument("--force-sixel", action="store_true", help="Force Sixel output even if not detected")
    parser.add_argument("--require-image", action="store_true", help="Exit non-zero if only ANSI fallback")
    parser.add_argument("--save-artifact", choices=["svg", "png"], metavar="FMT",
                        help="Save rendered Mermaid as SVG or PNG to artifacts dir (DEBUG/MANUAL path)")
    parser.add_argument("--save-path", metavar="PATH",
                        help="Override save location for --save-artifact")
    parser.add_argument("--open-external", action="store_true",
                        help="Open saved artifact externally via wslview/xdg-open (DEBUG/MANUAL only)")
    # Legacy alias
    parser.add_argument("--save-svg", metavar="PATH", help=argparse.SUPPRESS)
    parser.add_argument("--width", type=int, default=72)
    args = parser.parse_args()

    if args.force_kitty:
        os.environ["ANANTA_FORCE_KITTY"] = "1"
    if args.force_sixel:
        os.environ["ANANTA_FORCE_SIXEL"] = "1"

    caps = _print_capabilities()
    image_drawn = False

    if args.adapter in ("ansi", "all"):
        _render_ansi(width=args.width)

    if args.adapter in ("kitty", "all"):
        if _render_kitty(caps, force=args.force_kitty):
            image_drawn = True

    if args.adapter in ("sixel", "all"):
        if _render_sixel(caps, force=args.force_sixel):
            image_drawn = True

    # TGFX-014: External preview (debug/manual path)
    saved_path: Path | None = None
    if args.save_artifact:
        saved_path = _save_artifact(args.save_path, fmt=args.save_artifact)
        if saved_path and args.open_external:
            _open_external(saved_path)
    elif args.save_svg:
        # Legacy --save-svg support
        saved_path = _save_artifact(args.save_svg, fmt="svg")
        if saved_path and args.open_external:
            _open_external(saved_path)
    elif args.open_external:
        # Auto-save to temp dir when --open-external given without --save-artifact
        saved_path = _save_artifact(None, fmt="svg")
        if saved_path:
            _open_external(saved_path)

    _print_header("Summary")
    print(f"  Adapter:              {args.adapter}")
    print(f"  Image drawn:          {'✓ yes' if image_drawn else '✗ no (ANSI fallback)'}")
    print(f"  can_show_mermaid:     {'✓' if caps.get('can_show_mermaid_image') else '✗'}")
    if saved_path:
        print(f"  Artifact saved:       {saved_path.resolve()}")
    print()
    print("  To enable Kitty graphics: use Kitty, WezTerm or Ghostty terminal")
    print("                            or set ANANTA_FORCE_KITTY=1")
    print("  To enable Sixel:          set SIXEL_SUPPORTED=1 (+ pip install Pillow)")

    if args.require_image and not image_drawn:
        print("\n✗ --require-image: image rendering not available")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
