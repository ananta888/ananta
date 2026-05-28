#!/usr/bin/env python3
"""Manual smoke tool: render Markdown/Mermaid through ANSI and optional image paths.

Usage:
    python scripts/operator_tui_markdown_mermaid_smoke.py [path] [--mode ansi|source_only]
                                                          [--width N] [--height N] [--scroll N]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Operator TUI Markdown/Mermaid smoke renderer")
    parser.add_argument("path", nargs="?", help="Markdown file to render (reads stdin if omitted)")
    parser.add_argument("--mode", choices=["ansi", "source_only"], default="ansi")
    parser.add_argument("--width", type=int, default=80)
    parser.add_argument("--height", type=int, default=24)
    parser.add_argument("--scroll", type=int, default=0)
    args = parser.parse_args()

    try:
        from client_surfaces.operator_tui.visual.markdown.markdown_ansi_renderer import (
            MermaidFallbackInfo,
            render_markdown_ansi,
        )
        from client_surfaces.operator_tui.visual.markdown.markdown_parser import parse_markdown
        from client_surfaces.operator_tui.visual.markdown.mermaid_block_extractor import extract_mermaid_blocks
        from client_surfaces.operator_tui.visual.markdown.mermaid_renderer import MermaidRenderer
    except ImportError as exc:
        print(f"[ERROR] Cannot import operator_tui visual modules: {exc}", file=sys.stderr)
        print("Run from the project root: python scripts/operator_tui_markdown_mermaid_smoke.py", file=sys.stderr)
        sys.exit(1)

    if args.path:
        try:
            text = Path(args.path).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[ERROR] Cannot read file: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        text = sys.stdin.read()

    blocks = parse_markdown(text)
    renderer = MermaidRenderer()

    print("=== Mermaid renderer availability ===")
    for name, (ok, reason) in renderer.capability_status().items():
        status = "OK" if ok else f"UNAVAILABLE ({reason})"
        print(f"  {name}: {status}")
    print()

    mermaid_fallbacks: dict[str, MermaidFallbackInfo] = {}

    if args.mode == "ansi":
        extracted = extract_mermaid_blocks(blocks)
        if extracted:
            print(f"=== Mermaid blocks found: {len(extracted)} ===")
            for mb in extracted:
                result = renderer.render(mb.source)
                if result.success:
                    fmt = result.image_format
                    size = len(result.image_data or b"")
                    print(f"  [{mb.block_id}] OK: {fmt} {size}b in {result.duration_ms:.1f}ms")
                else:
                    print(f"  [{mb.block_id}] FALLBACK: {result.reason}")
                    mermaid_fallbacks[mb.source] = MermaidFallbackInfo(
                        source=mb.source,
                        reason=result.reason or "unavailable",
                    )
            print()

    lines = render_markdown_ansi(
        blocks,
        width=args.width,
        height=args.height,
        scroll_offset=args.scroll,
        mermaid_fallbacks=mermaid_fallbacks,
    )

    print(f"=== Rendered output ({args.width}x{args.height}, scroll={args.scroll}) ===")
    for line in lines:
        print(line)


if __name__ == "__main__":
    main()
