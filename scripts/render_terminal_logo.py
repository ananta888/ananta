#!/usr/bin/env python3
"""
Render ananta.svg → ANSI TrueColor half-block terminal art.

Usage:
  python scripts/render_terminal_logo.py
  python scripts/render_terminal_logo.py --width 120
  python scripts/render_terminal_logo.py --width 90 --mono-only
  python scripts/render_terminal_logo.py --svg path/to/file.svg --output-dir /tmp/out

Requires: Pillow, cairosvg (or rsvg-convert / inkscape)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

WHITE_LUMA = 240
FALLOFF = 10
COLOR_TOLERANCE = 25


def luma(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b


def pixel_alpha(r, g, b, a=255):
    if a < 200:
        return 0.0
    lum = luma(r, g, b)
    if lum >= WHITE_LUMA:
        return 0.0
    if lum >= WHITE_LUMA - FALLOFF:
        return (WHITE_LUMA - lum) / FALLOFF
    return 1.0


def col_dist(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def svg_to_png(svg_path: str, png_path: str, width: int = 800) -> None:
    try:
        from cairosvg import svg2png
        svg2png(url=svg_path, write_to=png_path, output_width=width, output_height=width)
        return
    except ImportError:
        pass
    try:
        subprocess.run(
            ["rsvg-convert", "--width", str(width), "--height", str(width), "-o", png_path, svg_path],
            check=True, capture_output=True,
        )
        return
    except FileNotFoundError:
        pass
    try:
        subprocess.run(
            ["inkscape", "--export-width", str(width), "--export-height", str(width),
             "--export-filename", png_path, svg_path],
            check=True, capture_output=True,
        )
        return
    except FileNotFoundError:
        pass
    print("Error: No SVG renderer found. Install cairosvg (`pip install cairosvg`), rsvg-convert, or inkscape.",
          file=sys.stderr)
    sys.exit(1)


def load_image(png_path: str, width: int, height_ratio: float = 0.48):
    from PIL import Image
    img = Image.open(png_path).convert("RGBA")
    aspect = img.height / img.width
    new_h = max(1, int(width * aspect * height_ratio))
    return img.resize((width, new_h), Image.LANCZOS)


def ansi_fg(r, g, b):
    return f"\x1b[38;2;{r};{g};{b}m"


def ansi_bg(r, g, b):
    return f"\x1b[48;2;{r};{g};{b}m"


def ansi_reset():
    return "\x1b[0m"


def render_ansi(img):
    px = list(img.getdata())
    w, h = img.size

    def g(x, y):
        return px[y * w + x]

    lines = []
    for y in range(0, h - 1, 2):
        parts = []
        last_fg = None
        last_bg = None
        x = 0
        while x < w:
            tr, tg, tb, ta = g(x, y)
            br, bg, bb, ba = g(x, y + 1)
            nta = pixel_alpha(tr, tg, tb, ta)
            nba = pixel_alpha(br, bg, bb, ba)
            tt = nta < 0.3
            bt = nba < 0.3

            if tt and bt:
                if last_fg is not None or last_bg is not None:
                    parts.append(ansi_reset())
                    last_fg = None
                    last_bg = None
                parts.append(" ")
                x += 1
                continue

            if tt and not bt:
                ch = "▄"
                fg = (br, bg, bb)
                bg_key = None
            elif not tt and bt:
                ch = "▀"
                fg = (tr, tg, tb)
                bg_key = None
            else:
                ch = "▀"
                fg = (tr, tg, tb)
                bg_key = (br, bg, bb)

            run = 1
            while x + run < w:
                ntr, ntg, ntb, nta2 = g(x + run, y)
                nbr, nbg, nbb, nba2 = g(x + run, y + 1)
                nta3 = pixel_alpha(ntr, ntg, ntb, nta2)
                nba3 = pixel_alpha(nbr, nbg, nbb, nba2)
                ntt = nta3 < 0.3
                nbt = nba3 < 0.3
                if ntt and nbt:
                    break
                if ntt and not nbt:
                    if col_dist((nbr, nbg, nbb), fg) <= COLOR_TOLERANCE and bg_key is None:
                        run += 1
                        continue
                elif not ntt and nbt:
                    if col_dist((ntr, ntg, ntb), fg) <= COLOR_TOLERANCE and bg_key is None:
                        run += 1
                        continue
                else:
                    if (col_dist((ntr, ntg, ntb), fg) <= COLOR_TOLERANCE and
                            col_dist((nbr, nbg, nbb), bg_key) <= COLOR_TOLERANCE):
                        run += 1
                        continue
                break

            if fg != last_fg or bg_key != last_bg:
                if last_fg is not None or last_bg is not None:
                    parts.append(ansi_reset())
                if bg_key:
                    parts.append(ansi_bg(*bg_key))
                parts.append(ansi_fg(*fg))
                last_fg = fg
                last_bg = bg_key

            parts.append(ch * run)
            x += run

        if last_fg is not None or last_bg is not None:
            parts.append(ansi_reset())
        lines.append("".join(parts))
    return "\n".join(lines)


def render_mono(img):
    px = list(img.getdata())
    w, h = img.size
    lines = []
    for y in range(0, h - 1, 2):
        line = ""
        for x in range(w):
            t = px[y * w + x]
            b = px[(y + 1) * w + x]
            tt = pixel_alpha(*t) < 0.3
            bt = pixel_alpha(*b) < 0.3
            if tt and bt:
                line += " "
            elif tt and not bt:
                line += "▄"
            elif not tt and bt:
                line += "▀"
            else:
                line += "▀" if luma(*t[:3]) > luma(*b[:3]) else "▄"
        lines.append(line)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render ananta.svg to ANSI terminal art.")
    parser.add_argument("--svg", default="ananta.svg", help="Path to source SVG (default: ananta.svg)")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: temp dir)")
    parser.add_argument("--width", type=int, nargs="+", default=[90], help="Output widths (default: 90)")
    parser.add_argument("--mono-only", action="store_true", help="Only generate monochrome fallback")
    parser.add_argument("--color-only", action="store_true", help="Only generate ANSI color output")
    args = parser.parse_args()

    svg_path = os.path.abspath(args.svg)
    if not os.path.exists(svg_path):
        print(f"Error: SVG not found at {svg_path}", file=sys.stderr)
        sys.exit(1)

    if args.output_dir:
        out_dir = os.path.abspath(args.output_dir)
        os.makedirs(out_dir, exist_ok=True)
    else:
        out_dir = tempfile.mkdtemp(prefix="ananta-logo-")

    with tempfile.TemporaryDirectory() as tmp:
        png_path = os.path.join(tmp, "rendered.png")
        svg_to_png(svg_path, png_path)

        for w in args.width:
            img = load_image(png_path, w)
            if not args.mono_only:
                art = render_ansi(img)
                dest = os.path.join(out_dir, f"ansi_halfblock_{w}.txt")
                with open(dest, "w") as f:
                    f.write(art)
                print(f"  -> {dest}")
            if not args.color_only:
                art_m = render_mono(img)
                dest = os.path.join(out_dir, f"mono_fallback_{w}.txt")
                with open(dest, "w") as f:
                    f.write(art_m)
                print(f"  -> {dest}")

    if not args.output_dir:
        print(f"\nOutput in temporary directory: {out_dir}")
        print("Use --output-dir to specify a permanent location.")


if __name__ == "__main__":
    main()
